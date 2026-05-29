"""
backend/app/socket/udp_listener.py
─────────────────────────────────────────────────────────────────────────────
백그라운드 UDP 소켓 리스너입니다.

각 UDPListener 인스턴스는 지정된 포트에 단일 UDP 소켓을 바인딩하고
recvfrom()을 지속적으로 호출하는 데몬 스레드를 실행합니다.

main.py에서 두 개의 인스턴스가 생성됩니다:
  • GingerbreadListener  — 포트 5000, Node B 패킷 (바이너리 Gingerbread)
  • PowerListener        — 포트 6000, ESP32-C3 JSON 전력 스트리밍

의존성 주입 (main.py에서 주입되며, 여기서 임포트되지 않음):
  ┌────────────────────────────────────────────────────────────┐
  │  UDPListener                                               │
  │    ↓ 원시 바이트(raw bytes)                                  │
  │  packet_parser  →  타입이 지정된 Packet (도착 타임스탬프 포함) │
  │    ↓                                                       │
  │  QoSHandler (Gingerbread 전용)  또는  직접 디스패치           │
  │    ↓ 확인된 PublishPacket / PowerPacket                       │
  │  SessionService  +  TelemetryService                       │
  └────────────────────────────────────────────────────────────┘

각 리스너에 노출되는 메트릭:
  .recv_count   — start() 이후 수신된 총 데이터그램 수
  .error_count  — start() 이후 총 디스패치/파싱 에러 수
  .is_alive     — 리스너 스레드가 실행 중인 동안 True
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
import socket
import threading
from typing import Callable, Optional

from app.models.packet import (
    ConnectPacket,
    DisconnectPacket,
    PowerPacket,
    PubrelPacket,
    PublishPacket,
)
from app.socket.packet_parser import parse_gingerbread_packet, parse_power_packet
from app.socket.qos_handler import QoSHandler
from config import UDP_BUFFER_SIZE

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# 기본 리스너
# ──────────────────────────────────────────────────────────────────────────────

class UDPListener:
    """
    데몬 스레드에서 실행되는 일반 UDP 리스너입니다.

    수신된 패킷을 처리하려면 하위 클래스를 만들고 _dispatch()를 오버라이드하세요.

    메트릭(Metrics)
    -------
    recv_count   : int — _dispatch()에 성공적으로 전달된 총 데이터그램 수
    error_count  : int — _dispatch()에서 예외를 발생시킨 총 데이터그램 수
    is_alive     : bool — 리스너 스레드가 실행 중인 동안 True
    """

    def __init__(self, host: str, port: int, name: str) -> None:
        self._host = host
        self._port = port
        self._name = name
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # ── 메트릭 ──────────────────────────────────────────────────────────
        self._recv_count:  int = 0
        self._error_count: int = 0
        self._metrics_lock = threading.Lock()

        # ── 소켓 ───────────────────────────────────────────────────────────
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self._host, self._port))
        # 차단(blocking)되는 recvfrom()이 정기적으로 시간 초과되도록 하여 stop_event를 존중하도록 허용합니다.
        self._sock.settimeout(1.0)

        logger.info("[%s] Bound to UDP %s:%d", self._name, self._host, self._port)

    # ── 수명 주기(Lifecycle) ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """데몬 리스너 스레드를 생성합니다."""
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name=f"udp-{self._name.lower()}",
        )
        self._thread.start()
        logger.info("[%s] Listener thread started.", self._name)

    def stop(self) -> None:
        """리스너 루프에 종료 신호를 보내고 소켓을 닫습니다."""
        self._stop_event.set()
        try:
            self._sock.close()
        except OSError:
            pass
        if self._thread:
            self._thread.join(timeout=3)
        logger.info("[%s] Listener stopped.", self._name)

    # ── 메트릭 속성(Metrics properties) ────────────────────────────────────────────────────

    @property
    def recv_count(self) -> int:
        """start() 이후 디스패치된 총 데이터그램 수입니다."""
        with self._metrics_lock:
            return self._recv_count

    @property
    def error_count(self) -> int:
        """start() 이후 총 디스패치 에러 수입니다."""
        with self._metrics_lock:
            return self._error_count

    @property
    def is_alive(self) -> bool:
        """리스너 스레드가 실행 중인 동안 True를 반환합니다."""
        return self._thread is not None and self._thread.is_alive()

    def get_metrics(self) -> dict:
        """메트릭 스냅샷을 일반 딕셔너리로 반환합니다."""
        with self._metrics_lock:
            return {
                "name":        self._name,
                "host":        self._host,
                "port":        self._port,
                "is_alive":    self.is_alive,
                "recv_count":  self._recv_count,
                "error_count": self._error_count,
            }

    # ── 내부 루프(Internal loop) ─────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        """차단(blocking) 수신 루프 — 데몬 스레드에서 실행됩니다."""
        while not self._stop_event.is_set():
            try:
                raw, addr = self._sock.recvfrom(UDP_BUFFER_SIZE)
            except socket.timeout:
                continue
            except OSError:
                # stop()에 의해 소켓이 닫혔습니다
                break
            except Exception as exc:
                logger.exception("[%s] Unexpected recv error: %s", self._name, exc)
                continue

            try:
                self._dispatch(raw, addr)
                with self._metrics_lock:
                    self._recv_count += 1
            except Exception as exc:
                with self._metrics_lock:
                    self._error_count += 1
                logger.error(
                    "[%s] Dispatch error for packet from %s: %s", self._name, addr, exc
                )

    def _dispatch(self, raw: bytes, addr) -> None:
        """수신된 데이터그램을 처리하려면 하위 클래스에서 오버라이드하세요."""
        raise NotImplementedError


# ──────────────────────────────────────────────────────────────────────────────
# Gingerbread 리스너  (Node B, 포트 5000)
# ──────────────────────────────────────────────────────────────────────────────

class GingerbreadListener(UDPListener):
    """
    Node B (ESP32-S3) Gingerbread UDP 패킷을 위해 포트 5000에서 수신 대기합니다.

    생성자를 통해 모든 의존성을 주입합니다.
    """

    def __init__(
        self,
        host: str,
        port: int,
        on_connect:    Callable[[ConnectPacket], None],
        on_disconnect: Callable[[DisconnectPacket], None],
        on_deliver:    Callable[[PublishPacket], None],
    ) -> None:
        super().__init__(host, port, name="Gingerbread")
        self._on_connect    = on_connect
        self._on_disconnect = on_disconnect

        # QoSHandler는 클라이언트에 PUBACK/PUBREC/PUBCOMP를 다시 보내는 데
        # 필요한 동일한 소켓 참조를 소유하도록 여기서 생성됩니다.
        self._qos_handler = QoSHandler(sock=self._sock, on_deliver=on_deliver)

    @property
    def qos_handler(self) -> QoSHandler:
        """진단 / 테스트를 위해 QoSHandler를 노출합니다."""
        return self._qos_handler

    def _dispatch(self, raw: bytes, addr) -> None:
        """원시 바이트를 파싱하고 세션 또는 QoS 핸들러로 라우팅합니다."""
        packet = parse_gingerbread_packet(raw, addr)

        if isinstance(packet, ConnectPacket):
            logger.info(
                "[Gingerbread] CONNECT from client_id='%s' @ %s", packet.client_id, addr
            )
            self._on_connect(packet)

        elif isinstance(packet, PublishPacket):
            logger.debug(
                "[Gingerbread] PUBLISH msg_id=%d qos=%d from %s",
                packet.msg_id, packet.qos, addr,
            )
            self._qos_handler.handle_publish(packet)

        elif isinstance(packet, DisconnectPacket):
            logger.info("[Gingerbread] DISCONNECT from %s", addr)
            self._on_disconnect(packet)

        elif isinstance(packet, PubrelPacket):
            logger.debug(
                "[Gingerbread] PUBREL msg_id=%d from %s", packet.msg_id, addr
            )
            self._qos_handler.handle_pubrel(packet)

        else:
            logger.warning("[Gingerbread] Unhandled packet type: %s", type(packet))


# ──────────────────────────────────────────────────────────────────────────────
# 전력 MCU 리스너  (ESP32-C3, 포트 6000)
# ──────────────────────────────────────────────────────────────────────────────

class PowerListener(UDPListener):
    """
    ESP32-C3 전력 MCU JSON 전력 스트리밍 패킷을 위해 포트 6000에서 수신 대기합니다.
    """

    def __init__(
        self,
        host: str,
        port: int,
        on_power: Callable[[PowerPacket], None],
    ) -> None:
        super().__init__(host, port, name="PowerMCU")
        self._on_power = on_power

    def _dispatch(self, raw: bytes, addr) -> None:
        packet = parse_power_packet(raw, addr)
        logger.debug(
            "[PowerMCU] Node=%s  %.2f mA  %.3f V  %.2f mW from %s",
            packet.node, packet.current_mA, packet.voltage_V, packet.power_mW, addr,
        )
        self._on_power(packet)
