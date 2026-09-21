"""
backend/app/socket/tcp_listener.py
─────────────────────────────────────────────────────────────────────────────
Gingerbread 상위 QoS 신뢰성 모드 — TCP 리스너.

프로젝트 명세
    Standard MQTT : 항상 TCP
    Gingerbread   : QoS 0·1 → UDP (udp_listener.py)
                    상위 QoS(펌웨어의 TCP_MIN_QOS 이상, 기본 QoS 2) → TCP (이 모듈)

TCP 와이어 포맷 (little-endian)
    디바이스 → 게이트웨이 : [len: uint16][PublishPacket 바이트 (len 바이트)]
    게이트웨이 → 디바이스 : PUBCOMP 4바이트 [len=4][type=7][msg_id: uint16]

  UDP는 데이터그램 경계가 있지만 TCP는 바이트 스트림이므로, 패킷 경계를 위해 2바이트
  길이 접두를 씁니다. (protocol.h의 Header.length는 uint8라 264바이트 PublishPacket을
  표현할 수 없어 사용할 수 없습니다.)

  본문의 PublishPacket 형식은 UDP와 동일하므로 packet_parser를 그대로 재사용합니다.

전달 보장
    1. 메시지를 서비스 계층에 전달(on_deliver)한 "뒤에" PUBCOMP를 보냅니다.
       전달에 실패하면 응답하지 않으므로 디바이스가 재전송합니다.
    2. 응답(PUBCOMP)이 유실되어 디바이스가 같은 메시지를 다시 보내도, (IP, msg_id)를
       TCP_DEDUP_TTL_SECONDS 동안 기억해 두 번 처리하지 않고 PUBCOMP만 다시 보냅니다.
       (TCP 위에서 "정확히 한 번" 전달 의미를 유지합니다.)

  UDP QoS 1 핸들러는 PUBACK을 먼저 보내고 전달하지만, 여기서는 신뢰성이 목적이므로
  전달 후 응답합니다. 전달은 프로세스 내부 호출이라 RTT 차이는 수 ms 수준입니다.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
import socket
import struct
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from app.models.packet import PublishPacket
from app.socket.packet_parser import parse_gingerbread_packet
from config import (
    MSG_PUBCOMP,
    TCP_CONN_TIMEOUT_SECONDS,
    TCP_DEDUP_TTL_SECONDS,
    TCP_MAX_FRAME,
)

logger = logging.getLogger(__name__)

Addr = Tuple[str, int]


def session_addr_for_ip(sessions: List[dict], ip: str) -> Optional[Addr]:
    """
    세션 목록에서 IP가 같은 세션의 (ip, port)를 반환합니다 (없으면 None).

    세션 테이블은 UDP CONNECT의 주소를 키로 삼는데, TCP 연결은 매번 임시 포트를 쓰므로
    IP만으로 디바이스를 찾아야 합니다. (한 IP에서 여러 디바이스를 돌리는 구성은 지원하지 않습니다.)
    """
    for sess in sessions:
        if sess.get("addr_ip") == ip:
            return (sess["addr_ip"], sess["addr_port"])
    return None


class GingerbreadTcpListener:
    """
    Gingerbread 상위 QoS TCP 데이터를 수신하는 리스너.

    Parameters
    ----------
    host, port     : 바인드 주소. port=0이면 OS가 빈 포트를 고르며 .port로 확인합니다.
    on_deliver     : 센서 데이터(topic 1) PublishPacket 전달 콜백.
    on_telemetry   : 실측 메트릭(topic 2) PublishPacket 전달 콜백 (없으면 on_deliver로 전달).
    conn_timeout   : 연결에서 다음 데이터를 기다리는 최대 시간(초).
    dedup_ttl      : 중복 메시지 판정 시간(초).
    """

    def __init__(
        self,
        host: str,
        port: int,
        on_deliver: Callable[[PublishPacket], None],
        on_telemetry: Optional[Callable[[PublishPacket], None]] = None,
        conn_timeout: float = TCP_CONN_TIMEOUT_SECONDS,
        dedup_ttl: float = TCP_DEDUP_TTL_SECONDS,
    ) -> None:
        self._name = "GingerbreadTCP"
        self._on_deliver = on_deliver
        self._on_telemetry = on_telemetry
        self._conn_timeout = conn_timeout
        self._dedup_ttl = dedup_ttl

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._lock = threading.Lock()
        self._seen: Dict[Tuple[str, int], float] = {}   # (ip, msg_id) → 만료 시각
        self._recv_count = 0
        self._duplicate_count = 0
        self._error_count = 0

        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((host, port))
        self._srv.listen(8)
        self._srv.settimeout(1.0)   # accept()가 주기적으로 깨어나 stop_event를 확인

        self._host = host
        self._port = self._srv.getsockname()[1]
        logger.info("[%s] Bound to TCP %s:%d", self._name, self._host, self._port)

    # ── 수명 주기 ────────────────────────────────────────────────────────────

    @property
    def port(self) -> int:
        return self._port

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._accept_loop, daemon=True, name="tcp-gingerbread",
        )
        self._thread.start()
        logger.info("[%s] Listener thread started.", self._name)

    def stop(self) -> None:
        self._stop_event.set()
        try:
            self._srv.close()
        except OSError:
            pass
        if self._thread:
            self._thread.join(timeout=3)
        logger.info("[%s] Listener stopped.", self._name)

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def get_metrics(self) -> dict:
        with self._lock:
            return {
                "name":            self._name,
                "host":            self._host,
                "port":            self._port,
                "is_alive":        self.is_alive,
                "recv_count":      self._recv_count,
                "duplicate_count": self._duplicate_count,
                "error_count":     self._error_count,
            }

    # ── 내부 루프 ────────────────────────────────────────────────────────────

    def _accept_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                conn, addr = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break   # stop()에 의해 소켓이 닫힘
            threading.Thread(
                target=self._handle_connection, args=(conn, addr),
                daemon=True, name="tcp-gingerbread-conn",
            ).start()

    def _handle_connection(self, conn: socket.socket, addr: Addr) -> None:
        """연결 하나를 처리합니다. 디바이스가 닫거나 오류가 나면 끝납니다."""
        try:
            conn.settimeout(self._conn_timeout)
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            while not self._stop_event.is_set():
                prefix = self._recv_exact(conn, 2)
                if prefix is None:
                    return   # 디바이스가 정상적으로 연결을 닫음
                (length,) = struct.unpack("<H", prefix)
                if length < 2 or length > TCP_MAX_FRAME:
                    raise ValueError(f"invalid frame length {length}")
                body = self._recv_exact(conn, length)
                if body is None:
                    raise ValueError("connection closed in the middle of a frame")
                self._process(conn, body, addr)
        except socket.timeout:
            logger.debug("[%s] idle timeout from %s", self._name, addr)
        except Exception as exc:
            with self._lock:
                self._error_count += 1
            logger.error("[%s] connection error from %s: %s", self._name, addr, exc)
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _process(self, conn: socket.socket, body: bytes, addr: Addr) -> None:
        packet = parse_gingerbread_packet(body, addr)   # 형식 오류는 ValueError → 연결 종료
        if not isinstance(packet, PublishPacket):
            logger.warning("[%s] non-PUBLISH packet ignored from %s: %s",
                           self._name, addr, type(packet).__name__)
            return

        key = (addr[0], packet.msg_id)
        if self._is_duplicate(key):
            with self._lock:
                self._duplicate_count += 1
            logger.warning("[%s] duplicate msg_id=%d from %s — PUBCOMP only.",
                           self._name, packet.msg_id, addr[0])
        else:
            if packet.topic_id == 2 and self._on_telemetry is not None:
                self._on_telemetry(packet)
            else:
                self._on_deliver(packet)
            self._remember(key)   # 전달이 성공한 뒤에만 기억 (실패하면 재전송을 받아야 함)
            with self._lock:
                self._recv_count += 1

        conn.sendall(struct.pack("<BBH", 4, MSG_PUBCOMP, packet.msg_id))

    # ── 중복 검사 ────────────────────────────────────────────────────────────

    def _is_duplicate(self, key: Tuple[str, int]) -> bool:
        now = time.monotonic()
        with self._lock:
            for k in [k for k, exp in self._seen.items() if exp <= now]:
                del self._seen[k]
            return key in self._seen

    def _remember(self, key: Tuple[str, int]) -> None:
        with self._lock:
            self._seen[key] = time.monotonic() + self._dedup_ttl

    # ── 유틸리티 ─────────────────────────────────────────────────────────────

    @staticmethod
    def _recv_exact(conn: socket.socket, n: int) -> Optional[bytes]:
        """
        정확히 n바이트를 읽습니다.
        - 첫 바이트 전에 상대가 정상적으로 닫으면 None (더 보낼 것이 없음)
        - 읽는 도중에 끊기면 ConnectionError (잘린 프레임을 정상 패킷으로 처리하지 않기 위함)
        """
        buf = b""
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                if not buf:
                    return None
                raise ConnectionError(f"connection closed after {len(buf)}/{n} bytes")
            buf += chunk
        return buf
