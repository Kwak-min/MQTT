"""
backend/app/models/session.py
─────────────────────────────────────────────────────────────────────────────
Data model for an ESP32 device session.

Lifecycle:
  ACTIVE     ← node woke up, sent CONNECT
  ASLEEP     ← node sent DISCONNECT (back to deep sleep)
  TIMED_OUT  ← server hasn't heard from the node in a while (future watchdog)
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Tuple
import time

Addr = Tuple[str, int]


class SessionStatus(Enum):
    ACTIVE    = auto()
    ASLEEP    = auto()
    TIMED_OUT = auto()


@dataclass
class Session:
    """
    Represents a single ESP32 device communication session.

    Attributes
    ----------
    client_id   : Unique 16-char device identifier from CONNECT packet.
    addr        : (ip, port) tuple of the sender.
    status      : Current lifecycle state.
    connected_at: Unix timestamp of the CONNECT packet.
    last_seen   : Unix timestamp of the most recent packet from this device.
    packet_count: Running total of PUBLISH packets received this session.
    """
    client_id:    str
    addr:         Addr
    status:       SessionStatus = SessionStatus.ACTIVE
    connected_at: float         = field(default_factory=time.time)
    last_seen:    float         = field(default_factory=time.time)
    packet_count: int           = 0

    def touch(self) -> None:
        """Update last_seen to now."""
        self.last_seen = time.time()

    def to_dict(self) -> dict:
        """Serialise to a plain dict (for REST responses / CSV rows)."""
        return {
            "client_id":    self.client_id,
            "addr_ip":      self.addr[0],
            "addr_port":    self.addr[1],
            "status":       self.status.name,
            "connected_at": self.connected_at,
            "last_seen":    self.last_seen,
            "packet_count": self.packet_count,
        }
