"""
backend/app/models/packet.py
─────────────────────────────────────────────────────────────────────────────
Gingerbread 및 전력 MCU 프로토콜의 모든 패킷 유형을 나타내는 
순수 데이터 데이터클래스(Pure-data dataclasses)입니다.

이것들은 백엔드의 모든 계층을 통해 흐르는 정규 타입 형태(canonical typed forms)입니다.
여기에는 로직이 없습니다 — 구조와 가벼운 직렬화(serialisation)만 있습니다.

v1에서의 변경 사항:
  • 이제 모든 패킷에 `timestamp`(서버 측 도착 시간, Unix float)가 포함됩니다.
  • PublishPacket, PowerPacket, ConnectPacket, DisconnectPacket은 모두
    REST 엔드포인트 및 향후 DB 어댑터에서 사용되는 `to_dict()` 헬퍼를 노출합니다.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple, Union

# UDP (host, port) 쌍을 위한 별칭(Alias)
Addr = Tuple[str, int]


# ──────────────────────────────────────────────────────────────────────────────
# Gingerbread 프로토콜 패킷  (Node B, 포트 5000)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ConnectPacket:
    """
    ESP32-S3(Node B)가 깊은 수면(deep sleep)에서 깨어나 연결될 때 전송됩니다.

    바이너리 레이아웃 (리틀 엔디안):
      [0]     header_len  : uint8  — 전체 패킷 길이
      [1]     msg_type    : uint8  — 항상 1 (MSG_CONNECT)
      [2:18]  client_id   : char[16] — 널 패딩된 ASCII 디바이스 식별자
    """
    addr:       Addr
    client_id:  str
    raw:        bytes          = field(repr=False)
    timestamp:  float          = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "packet_type": "CONNECT",
            "addr_ip":     self.addr[0],
            "addr_port":   self.addr[1],
            "client_id":   self.client_id,
            "timestamp":   self.timestamp,
        }


@dataclass
class PublishPacket:
    """
    Node B에서 센서 페이로드를 전달합니다.

    바이너리 레이아웃 (리틀 엔디안):
      [0]     header_len  : uint8
      [1]     msg_type    : uint8  — 항상 2 (MSG_PUBLISH)
      [2:4]   msg_id      : uint16 — 단조롭게 증가하는 메시지 ID
      [4]     qos         : uint8  — 0, 1, 또는 2
      [5:7]   topic_id    : uint16 — 숫자 형태의 토픽 식별자
      [7:]    payload     : UTF-8 JSON 문자열 (temp, hum, gas 필드)
    """
    addr:        Addr
    msg_id:      int
    qos:         int                     # 0, 1, or 2
    topic_id:    int
    payload_raw: str                     # 네트워크에서 수신된 원시 JSON 문자열
    payload:     Optional[dict] = None   # 파싱된 딕셔너리, packet_parser에 의해 채워짐
    timestamp:   float          = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        p = self.payload or {}
        return {
            "packet_type": "PUBLISH",
            "addr_ip":     self.addr[0],
            "addr_port":   self.addr[1],
            "msg_id":      self.msg_id,
            "qos":         self.qos,
            "topic_id":    self.topic_id,
            "timestamp":   self.timestamp,
            "temp":        p.get("temp"),
            "hum":         p.get("hum"),
            "gas":         p.get("gas"),
            "power":       p.get("power"),
            "payload_raw": self.payload_raw,
        }


@dataclass
class DisconnectPacket:
    """
    Node B가 깊은 수면(deep sleep)에 다시 들어가기 전에 전송됩니다.

    바이너리 레이아웃:
      [0]  header_len : uint8
      [1]  msg_type   : uint8 — 항상 4 (MSG_DISCONNECT)
    """
    addr:      Addr
    raw:       bytes = field(repr=False)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "packet_type": "DISCONNECT",
            "addr_ip":     self.addr[0],
            "addr_port":   self.addr[1],
            "timestamp":   self.timestamp,
        }


# ──────────────────────────────────────────────────────────────────────────────
# QoS 2 제어 패킷  (클라이언트 → 서버)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class PubrelPacket:
    """
    QoS 2 단계 2 — 클라이언트가 PUBREC를 확인하고(acknowledges) 메시지를 해제합니다(releases).

    바이너리 레이아웃:
      [0]    header_len : uint8
      [1]    msg_type   : uint8 — 항상 6 (MSG_PUBREL)
      [2:4]  msg_id     : uint16
    """
    addr:      Addr
    msg_id:    int
    raw:       bytes = field(repr=False)
    timestamp: float = field(default_factory=time.time)


# ──────────────────────────────────────────────────────────────────────────────
# 전력 MCU 프로토콜 패킷  (ESP32-C3, 포트 6000)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class PowerPacket:
    """
    ESP32-C3 전력 MCU에서 전송되는 JSON 전력 스트리밍 패킷입니다.

    예상되는 JSON 키:
      node        : "A" | "B"
      current_mA  : float
      voltage_V   : float
      power_mW    : float

    패킷은 바이너리 헤더가 없는 원시(raw) UTF-8 JSON으로 도착합니다.
    """
    addr:        Addr
    node:        str      # "A" or "B"
    current_mA:  float
    voltage_V:   float
    power_mW:    float
    raw:         str   = field(repr=False)    # 원본 JSON 문자열
    timestamp:   float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "packet_type": "POWER",
            "addr_ip":     self.addr[0],
            "addr_port":   self.addr[1],
            "node":        self.node,
            "current_mA":  self.current_mA,
            "voltage_V":   self.voltage_V,
            "power_mW":    self.power_mW,
            "timestamp":   self.timestamp,
        }


# ──────────────────────────────────────────────────────────────────────────────
# 코드베이스 전체에서 사용되는 유니온 타입 힌트
# ──────────────────────────────────────────────────────────────────────────────

AnyPacket = Union[ConnectPacket, PublishPacket, DisconnectPacket, PubrelPacket, PowerPacket]
