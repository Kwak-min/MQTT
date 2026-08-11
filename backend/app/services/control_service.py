"""
backend/app/services/control_service.py
─────────────────────────────────────────────────────────────────────────────
다운링크 제어 서비스 — 대시보드에서 ESP32-S3 디바이스로의 UDP 다운링크 전송.

protocol.h의 PublishPacket 바이너리 규격(#pragma pack(push, 1))에 맞춰
리틀 엔디안으로 바이트를 패킹한 후 지정된 IP/Port로 UDP 전송합니다.

다운링크 패킷 레이아웃 (136 bytes, little-endian):
  Offset  Size   Field           Description
  ──────  ─────  ──────────────  ────────────────────────────────
  0       1      length          전체 패킷 바이트 수 (uint8)
  1       1      msg_type        MSG_PUBLISH = 2 (uint8)
  2       2      msg_id          메시지 ID (uint16, LE)
  4       1      qos             QoS 레벨 (uint8: 0, 1, 2)
  5       2      topic_id        제어 토픽 = 0xFFFF (uint16, LE)
  7       128    payload         JSON + 널패딩 (char[128])
  135     1      meta            비트필드 (uint8, 0x00)
  ──────────────────────────────────────────────────────────────
  Total: 136 bytes (PublishPacket #pragma pack(1) 규격 일치)

의존성:
  이 서비스는 외부 상태에 의존하지 않는 순수 네트워크 유틸리티입니다.
  main.py에서 인스턴스를 생성하고 control_controller에 주입됩니다.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
import logging
import socket
import struct
import threading
from typing import Any, Dict

from config import MSG_PUBLISH

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# 다운링크 패킷 상수 (protocol.h PublishPacket 구조체와 일치)
# ──────────────────────────────────────────────────────────────────────────────

_DOWNLINK_TOPIC_ID: int = 0xFFFF       # 다운링크 제어 전용 토픽 ID
_PAYLOAD_SIZE:      int = 128          # char payload[128]
_PACKET_TOTAL_LEN:  int = 136          # Header(2) + msg_id(2) + qos(1) + topic_id(2) + payload(128) + meta(1)
_META_DEFAULT:      int = 0x00         # 비트필드 기본값 (network_status=GOOD, data_urgency=NORMAL)

# UDP 전송 타임아웃 (초)
_UDP_SEND_TIMEOUT:  float = 3.0


class ControlService:
    """
    대시보드 → ESP32-S3 다운링크 제어 패킷을 구성하고 UDP로 전송하는 서비스.

    스레드 안전성:
      msg_id 카운터는 Lock으로 보호됩니다. 여러 대시보드 사용자가
      동시에 제어 명령을 보내도 패킷 ID가 중복되지 않습니다.

    사용 예시:
      svc = ControlService()
      result = svc.send_downlink(
          device_ip="192.168.1.42",
          device_port=8888,
          qos_level=1,
          sleep_interval=1000,
      )
    """

    def __init__(self) -> None:
        self._msg_id_counter: int = 0
        self._lock = threading.Lock()
        logger.info("[제어] ControlService 초기화 완료.")

    # ──────────────────────────────────────────────────────────────────────────
    # 퍼블릭 인터페이스
    # ──────────────────────────────────────────────────────────────────────────

    def send_downlink(
        self,
        device_ip: str,
        device_port: int,
        qos_level: int,
        sleep_interval: int,
    ) -> Dict[str, Any]:
        """
        ESP32-S3 디바이스로 다운링크 제어 패킷을 UDP 전송합니다.

        매개변수
        --------
        device_ip      : 대상 디바이스 IP 주소 (예: "192.168.1.42").
        device_port    : 대상 디바이스 UDP 포트 (예: 8888).
        qos_level      : 설정할 QoS 레벨 (0, 1, 2).
        sleep_interval : 슬립 간격 (밀리초, 양의 정수).

        반환값
        ------
        dict — 전송 결과:
          {
            "status": "ok",
            "msg_id": 42,
            "packet_size": 136,
            "target": "192.168.1.42:8888"
          }

        예외
        ----
        ValueError : 입력값 유효성 검증 실패.
        OSError    : UDP 소켓 전송 실패.
        """
        # ── 1단계: 입력값 유효성 검증 ──────────────────────────────────────
        self._validate_inputs(device_ip, device_port, qos_level, sleep_interval)

        # ── 2단계: msg_id 발급 (스레드 안전) ──────────────────────────────
        with self._lock:
            self._msg_id_counter += 1
            # uint16 범위 순환 (0~65535)
            msg_id = self._msg_id_counter % 65536

        # ── 3단계: 페이로드 JSON 구성 ─────────────────────────────────────
        payload_dict = {
            "qos_level":      qos_level,
            "sleep_interval": sleep_interval,
        }
        payload_json = json.dumps(payload_dict, separators=(",", ":"))

        # 128바이트 버퍼에 JSON을 널 패딩하여 삽입
        payload_bytes = self._pack_payload(payload_json)

        # ── 4단계: protocol.h PublishPacket 바이너리 패킹 ──────────────────
        packet = self._build_packet(msg_id, qos_level, payload_bytes)

        # ── 5단계: UDP 전송 ───────────────────────────────────────────────
        self._send_udp(packet, device_ip, device_port)

        target_str = f"{device_ip}:{device_port}"
        logger.info(
            "[제어] 다운링크 전송 완료 | msg_id=%d qos=%d sleep=%d ms | target=%s | %d bytes",
            msg_id, qos_level, sleep_interval, target_str, len(packet),
        )

        return {
            "status":      "ok",
            "msg_id":      msg_id,
            "packet_size": len(packet),
            "target":      target_str,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # 내부 메서드
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _validate_inputs(
        device_ip: str,
        device_port: int,
        qos_level: int,
        sleep_interval: int,
    ) -> None:
        """입력값 유효성 검증. 실패 시 ValueError를 발생시킵니다."""
        # IP 형식 검증
        try:
            socket.inet_aton(device_ip)
        except socket.error:
            raise ValueError(
                f"유효하지 않은 IP 주소입니다: '{device_ip}'"
            )

        # 포트 범위 검증
        if not isinstance(device_port, int) or not (1 <= device_port <= 65535):
            raise ValueError(
                f"포트 번호는 1~65535 사이의 정수여야 합니다: {device_port}"
            )

        # QoS 레벨 검증
        if qos_level not in (0, 1, 2):
            raise ValueError(
                f"QoS 레벨은 0, 1, 2 중 하나여야 합니다: {qos_level}"
            )

        # 슬립 간격 검증
        if not isinstance(sleep_interval, int) or sleep_interval <= 0:
            raise ValueError(
                f"슬립 간격은 양의 정수(ms)여야 합니다: {sleep_interval}"
            )

    @staticmethod
    def _pack_payload(payload_json: str) -> bytes:
        """
        JSON 문자열을 128바이트 고정 길이 버퍼로 패킹합니다.
        남은 공간은 0x00(널)으로 패딩됩니다.

        매개변수
        --------
        payload_json : JSON 직렬화된 문자열.

        반환값
        ------
        bytes — 정확히 128바이트.

        예외
        ----
        ValueError : JSON 문자열이 128바이트를 초과하는 경우.
        """
        encoded = payload_json.encode("utf-8")
        if len(encoded) > _PAYLOAD_SIZE:
            raise ValueError(
                f"페이로드가 {_PAYLOAD_SIZE}바이트를 초과합니다: "
                f"{len(encoded)}바이트 (내용: {payload_json[:50]}...)"
            )
        # 널 패딩하여 정확히 128바이트로 맞춤
        return encoded.ljust(_PAYLOAD_SIZE, b"\x00")

    @staticmethod
    def _build_packet(msg_id: int, qos_level: int, payload_bytes: bytes) -> bytes:
        """
        protocol.h PublishPacket 구조체와 동일한 바이너리 패킷을 구성합니다.

        레이아웃 (리틀 엔디안, #pragma pack(push, 1)):
          Header:   length(uint8) + msg_type(uint8)
          msg_id:   uint16
          qos:      uint8
          topic_id: uint16
          payload:  char[128]
          meta:     uint8 (비트필드)

        매개변수
        --------
        msg_id        : 메시지 식별자 (0~65535).
        qos_level     : QoS 레벨 (0, 1, 2).
        payload_bytes : 128바이트 페이로드 버퍼.

        반환값
        ------
        bytes — 136바이트 패킷.
        """
        # Header: length(uint8) + msg_type(uint8)
        # msg_id: uint16 (LE)
        # qos: uint8
        # topic_id: uint16 (LE)
        header = struct.pack(
            "<BBHBH",
            _PACKET_TOTAL_LEN,     # length — 전체 패킷 길이
            MSG_PUBLISH,           # msg_type = 2
            msg_id,                # msg_id (uint16, LE)
            qos_level,             # qos (uint8)
            _DOWNLINK_TOPIC_ID,    # topic_id = 0xFFFF (uint16, LE)
        )

        # 비트필드 메타 바이트: network_status(2b) + data_urgency(2b) + reserved(4b)
        meta = struct.pack("<B", _META_DEFAULT)

        packet = header + payload_bytes + meta
        assert len(packet) == _PACKET_TOTAL_LEN, (
            f"패킷 크기 불일치: expected={_PACKET_TOTAL_LEN}, got={len(packet)}"
        )
        return packet

    @staticmethod
    def _send_udp(packet: bytes, ip: str, port: int) -> None:
        """
        UDP 소켓을 통해 패킷을 대상 IP/Port로 전송합니다.

        매개변수
        --------
        packet : 전송할 바이너리 데이터.
        ip     : 대상 IP 주소.
        port   : 대상 UDP 포트.

        예외
        ----
        OSError : 소켓 생성 또는 전송 실패.
        """
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(_UDP_SEND_TIMEOUT)
            sock.sendto(packet, (ip, port))
        except socket.timeout as exc:
            logger.error(
                "[제어] UDP 전송 타임아웃: target=%s:%d, error=%s", ip, port, exc
            )
            raise OSError(f"UDP 전송 타임아웃 ({_UDP_SEND_TIMEOUT}초): {ip}:{port}") from exc
        except OSError as exc:
            logger.error(
                "[제어] UDP 전송 실패: target=%s:%d, error=%s", ip, port, exc
            )
            raise
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
