"""
backend/app/socket/packet_parser.py
─────────────────────────────────────────────────────────────────────────────
Translates raw UDP bytes into typed Packet dataclasses.

This module is a pure-function boundary between the OS socket and the rest
of the backend.  It knows about wire formats but nothing about business logic.

Gingerbread wire format (little-endian):
  Byte 0 : header_len (uint8)
  Byte 1 : msg_type   (uint8)
  Bytes 2+: type-specific payload

Power MCU format:
  Raw UTF-8 JSON string (no binary header)
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
import struct
import logging
from typing import Tuple

from app.models.packet import (
    AnyPacket,
    ConnectPacket,
    DisconnectPacket,
    PowerPacket,
    PubrelPacket,
    PublishPacket,
)
from config import (
    CONNECT_MIN_LEN,
    MSG_CONNECT,
    MSG_DISCONNECT,
    MSG_PUBLISH,
    MSG_PUBREL,
    PUBLISH_MIN_LEN,
)

logger = logging.getLogger(__name__)

Addr = Tuple[str, int]


# ──────────────────────────────────────────────────────────────────────────────
# Public interface
# ──────────────────────────────────────────────────────────────────────────────

def parse_gingerbread_packet(raw: bytes, addr: Addr) -> AnyPacket:
    """
    Parse a raw UDP datagram from Node B (port 5000) into a typed dataclass.

    Parameters
    ----------
    raw  : Raw bytes received from recvfrom().
    addr : (ip, port) of the sender.

    Returns
    -------
    One of: ConnectPacket, PublishPacket, DisconnectPacket, PubrelPacket.

    Raises
    ------
    ValueError  : Packet is too short or contains an unknown msg_type.
    """
    if len(raw) < 2:
        raise ValueError(f"Datagram too short ({len(raw)} bytes) from {addr}")

    _header_len, msg_type = struct.unpack_from("<BB", raw, 0)

    if msg_type == MSG_CONNECT:
        return _parse_connect(raw, addr)

    elif msg_type == MSG_PUBLISH:
        return _parse_publish(raw, addr)

    elif msg_type == MSG_DISCONNECT:
        return _parse_disconnect(raw, addr)

    elif msg_type == MSG_PUBREL:
        return _parse_pubrel(raw, addr)

    else:
        raise ValueError(f"Unknown msg_type={msg_type} from {addr}")


def parse_power_packet(raw: bytes, addr: Addr) -> PowerPacket:
    """
    Parse a raw UDP datagram from the ESP32-C3 Power MCU (port 6000).

    The Power MCU sends plain UTF-8 JSON with no binary header:
      {"node": "A", "current_mA": 12.5, "voltage_V": 4.98, "power_mW": 62.3}

    Parameters
    ----------
    raw  : Raw bytes from recvfrom().
    addr : (ip, port) of the sender.

    Returns
    -------
    PowerPacket

    Raises
    ------
    ValueError : JSON is malformed or required keys are missing.
    """
    raw_str = raw.decode("utf-8", errors="ignore").rstrip("\x00")

    try:
        data = json.loads(raw_str)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON from Power MCU {addr}: {exc}") from exc

    required_keys = {"node", "current_mA", "voltage_V", "power_mW"}
    missing = required_keys - data.keys()
    if missing:
        raise ValueError(f"Power packet missing keys {missing} from {addr}")

    return PowerPacket(
        addr=addr,
        node=str(data["node"]),
        current_mA=float(data["current_mA"]),
        voltage_V=float(data["voltage_V"]),
        power_mW=float(data["power_mW"]),
        raw=raw_str,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Private per-type parsers
# ──────────────────────────────────────────────────────────────────────────────

def _parse_connect(raw: bytes, addr: Addr) -> ConnectPacket:
    if len(raw) < CONNECT_MIN_LEN:
        raise ValueError(
            f"CONNECT packet too short: got {len(raw)}, need {CONNECT_MIN_LEN}"
        )
    client_id = raw[2:18].decode("utf-8", errors="ignore").rstrip("\x00")
    return ConnectPacket(addr=addr, client_id=client_id, raw=raw)


def _parse_publish(raw: bytes, addr: Addr) -> PublishPacket:
    if len(raw) < PUBLISH_MIN_LEN:
        raise ValueError(
            f"PUBLISH packet too short: got {len(raw)}, need {PUBLISH_MIN_LEN}"
        )
    # <BB   → header_len, msg_type
    # <H    → msg_id  (uint16)
    # <B    → qos     (uint8)
    # <H    → topic_id(uint16)
    _, _, msg_id, qos, topic_id = struct.unpack_from("<BBHBH", raw, 0)

    payload_raw = raw[PUBLISH_MIN_LEN:].decode("utf-8", errors="ignore").rstrip("\x00")

    # Attempt JSON parse; keep raw string if it fails
    payload_dict = None
    try:
        payload_dict = json.loads(payload_raw)
    except json.JSONDecodeError:
        logger.warning("PUBLISH payload is not valid JSON from %s: %r", addr, payload_raw)

    return PublishPacket(
        addr=addr,
        msg_id=msg_id,
        qos=qos,
        topic_id=topic_id,
        payload_raw=payload_raw,
        payload=payload_dict,
    )


def _parse_disconnect(raw: bytes, addr: Addr) -> DisconnectPacket:
    return DisconnectPacket(addr=addr, raw=raw)


def _parse_pubrel(raw: bytes, addr: Addr) -> PubrelPacket:
    if len(raw) < 4:
        raise ValueError(f"PUBREL packet too short: got {len(raw)}, need 4")
    _, _, msg_id = struct.unpack_from("<BBH", raw, 0)
    return PubrelPacket(addr=addr, msg_id=msg_id, raw=raw)
