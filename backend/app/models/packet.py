"""
backend/app/models/packet.py
─────────────────────────────────────────────────────────────────────────────
Pure-data dataclasses representing every packet type in the Gingerbread
and Power-MCU protocols.

These are the canonical typed forms that flow through every layer of the
backend.  NO logic lives here — only structure and lightweight serialisation.

Changes from v1:
  • Every packet now carries a `timestamp` (server-side arrival time, Unix float).
  • PublishPacket, PowerPacket, ConnectPacket, DisconnectPacket all expose a
    `to_dict()` helper used by REST endpoints and future DB adapters.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple, Union

# Alias for a UDP (host, port) pair
Addr = Tuple[str, int]


# ──────────────────────────────────────────────────────────────────────────────
# Gingerbread Protocol Packets  (Node B, Port 5000)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ConnectPacket:
    """
    Sent by ESP32-S3 (Node B) when it wakes from deep sleep and connects.

    Binary layout (little-endian):
      [0]     header_len  : uint8  — total packet length
      [1]     msg_type    : uint8  — always 1 (MSG_CONNECT)
      [2:18]  client_id   : char[16] — null-padded ASCII device identifier
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
    Carries sensor payload from Node B.

    Binary layout (little-endian):
      [0]     header_len  : uint8
      [1]     msg_type    : uint8  — always 2 (MSG_PUBLISH)
      [2:4]   msg_id      : uint16 — monotonically incrementing message ID
      [4]     qos         : uint8  — 0, 1, or 2
      [5:7]   topic_id    : uint16 — numeric topic identifier
      [7:]    payload     : UTF-8 JSON string (temp, hum, gas fields)
    """
    addr:        Addr
    msg_id:      int
    qos:         int                     # 0, 1, or 2
    topic_id:    int
    payload_raw: str                     # raw JSON string from wire
    payload:     Optional[dict] = None   # parsed dict, filled by packet_parser
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
    Sent by Node B before re-entering deep sleep.

    Binary layout:
      [0]  header_len : uint8
      [1]  msg_type   : uint8 — always 4 (MSG_DISCONNECT)
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
# QoS 2 Control Packets  (client → server)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class PubrelPacket:
    """
    QoS 2 step 2 — client acknowledges the PUBREC and releases the message.

    Binary layout:
      [0]    header_len : uint8
      [1]    msg_type   : uint8 — always 6 (MSG_PUBREL)
      [2:4]  msg_id     : uint16
    """
    addr:      Addr
    msg_id:    int
    raw:       bytes = field(repr=False)
    timestamp: float = field(default_factory=time.time)


# ──────────────────────────────────────────────────────────────────────────────
# Power MCU Protocol Packets  (ESP32-C3, Port 6000)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class PowerPacket:
    """
    JSON power-streaming packet from the ESP32-C3 Power MCU.

    Expected JSON keys:
      node        : "A" | "B"
      current_mA  : float
      voltage_V   : float
      power_mW    : float

    The packet arrives as raw UTF-8 JSON (no binary header).
    """
    addr:        Addr
    node:        str      # "A" or "B"
    current_mA:  float
    voltage_V:   float
    power_mW:    float
    raw:         str   = field(repr=False)    # original JSON string
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
# Union type hint used across the codebase
# ──────────────────────────────────────────────────────────────────────────────

AnyPacket = Union[ConnectPacket, PublishPacket, DisconnectPacket, PubrelPacket, PowerPacket]
