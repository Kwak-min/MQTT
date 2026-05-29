"""
backend/app/socket/qos_handler.py
─────────────────────────────────────────────────────────────────────────────
Protocol Controller — QoS 0 / 1 / 2 Handshake State Machine.

This class is the single authority on all QoS protocol responses.
It is instantiated once (in udp_listener.py) and shared across all
incoming PUBLISH packets on a given socket.

QoS Level Summary
─────────────────
  QoS 0  →  Fire and forget.  No ack.  Payload is returned immediately.

  QoS 1  →  At-least-once delivery.
             Server  sends  PUBACK  (msg_type=3) to client.

  QoS 2  →  Exactly-once delivery (4-way handshake):
             1. Client → Server : PUBLISH   (msg_type=2, qos=2)
             2. Server → Client : PUBREC    (msg_type=5)   ← step recorded
             3. Client → Server : PUBREL    (msg_type=6)   ← triggers PUBCOMP
             4. Server → Client : PUBCOMP   (msg_type=7)   ← session released

Packet wire format (all little-endian):
  PUBACK   : [len=4, type=3, msg_id: uint16]
  PUBREC   : [len=4, type=5, msg_id: uint16]
  PUBCOMP  : [len=4, type=7, msg_id: uint16]

Diagnostics
───────────
  handler.get_diagnostics() → dict with counters for all QoS operations.
  Exposed via GET /api/diagnostics in the REST layer.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
import socket
import struct
import threading
import time
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Tuple

from app.models.packet import PublishPacket, PubrelPacket
from config import (
    MSG_PUBACK,
    MSG_PUBCOMP,
    MSG_PUBREC,
    QOS2_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)

Addr = Tuple[str, int]

# Callback signature: (PublishPacket) → None
# Called by QoSHandler when a message is finally confirmed and ready to deliver
# to the service layer.
DeliverCallback = Callable[[PublishPacket], None]


# ──────────────────────────────────────────────────────────────────────────────
# QoS 2 in-flight state
# ──────────────────────────────────────────────────────────────────────────────

class QoS2State(Enum):
    AWAITING_PUBREL  = auto()   # Server sent PUBREC, waiting for client PUBREL
    PUBREL_RECEIVED  = auto()   # Client sent PUBREL, server sent PUBCOMP


@dataclass
class QoS2Record:
    """Tracks a single in-flight QoS 2 message."""
    msg_id:     int
    addr:       Addr
    packet:     PublishPacket
    state:      QoS2State = QoS2State.AWAITING_PUBREL
    created_at: float     = field(default_factory=time.time)


# ──────────────────────────────────────────────────────────────────────────────
# QoSHandler
# ──────────────────────────────────────────────────────────────────────────────

class QoSHandler:
    """
    Stateful QoS protocol controller.

    Parameters
    ----------
    sock         : The bound UDP socket used to send ack packets back.
    on_deliver   : Callback invoked when a message is ready for the service layer.
                   Called with the confirmed PublishPacket.
    """

    def __init__(self, sock: socket.socket, on_deliver: DeliverCallback) -> None:
        self._sock       = sock
        self._on_deliver = on_deliver
        self._lock       = threading.Lock()

        # { msg_id → QoS2Record }
        self._qos2_pending: Dict[int, QoS2Record] = {}

        # ── Counters (protected by _lock) ─────────────────────────────────
        self._total_delivered:   int = 0
        self._total_puback_sent: int = 0
        self._total_pubrec_sent: int = 0
        self._total_pubcomp_sent: int = 0
        self._total_expired:     int = 0

        # Start background GC thread to expire stale QoS 2 records
        self._gc_thread = threading.Thread(
            target=self._gc_loop, daemon=True, name="qos2-gc"
        )
        self._gc_thread.start()

    # ──────────────────────────────────────────────────────────────────────────
    # Public — called by udp_listener.py
    # ──────────────────────────────────────────────────────────────────────────

    def handle_publish(self, packet: PublishPacket) -> None:
        """
        Entry point for an incoming PUBLISH packet.
        Dispatches to the appropriate QoS handler and may invoke on_deliver.
        """
        qos = packet.qos

        if qos == 0:
            self._handle_qos0(packet)
        elif qos == 1:
            self._handle_qos1(packet)
        elif qos == 2:
            self._handle_qos2_publish(packet)
        else:
            logger.warning(
                "Unknown QoS level %d from %s, dropping packet.", qos, packet.addr
            )

    def handle_pubrel(self, packet: PubrelPacket) -> None:
        """
        Entry point for an incoming PUBREL packet (QoS 2 step 2).
        Sends PUBCOMP and delivers the original PUBLISH to the service layer.
        """
        msg_id = packet.msg_id
        addr   = packet.addr

        with self._lock:
            record = self._qos2_pending.get(msg_id)

        if record is None:
            logger.warning(
                "PUBREL for unknown msg_id=%d from %s — may have already expired.",
                msg_id, addr,
            )
            return

        if record.state != QoS2State.AWAITING_PUBREL:
            logger.warning(
                "Duplicate PUBREL for msg_id=%d from %s — ignoring.", msg_id, addr
            )
            return

        # Update state before sending PUBCOMP (so duplicate PUBREL is harmless)
        with self._lock:
            record.state = QoS2State.PUBREL_RECEIVED

        self._send_pubcomp(addr, msg_id)
        logger.info(
            "[QoS 2] PUBCOMP sent for msg_id=%d to %s — message delivered.", msg_id, addr
        )

        # Deliver to service layer
        self._on_deliver(record.packet)
        with self._lock:
            self._total_delivered += 1
            self._qos2_pending.pop(msg_id, None)

    # ──────────────────────────────────────────────────────────────────────────
    # Diagnostics — exposed by GET /api/diagnostics
    # ──────────────────────────────────────────────────────────────────────────

    def get_diagnostics(self) -> dict:
        """Return a snapshot of all QoS counters as a plain dict."""
        with self._lock:
            return {
                "pending_qos2_count":   len(self._qos2_pending),
                "total_delivered":      self._total_delivered,
                "total_puback_sent":    self._total_puback_sent,
                "total_pubrec_sent":    self._total_pubrec_sent,
                "total_pubcomp_sent":   self._total_pubcomp_sent,
                "total_qos2_expired":   self._total_expired,
            }

    @property
    def pending_qos2_count(self) -> int:
        """Number of QoS 2 messages awaiting PUBREL."""
        with self._lock:
            return len(self._qos2_pending)

    # ──────────────────────────────────────────────────────────────────────────
    # QoS level implementations
    # ──────────────────────────────────────────────────────────────────────────

    def _handle_qos0(self, packet: PublishPacket) -> None:
        """QoS 0: Fire-and-forget. Deliver immediately, no ack."""
        logger.debug(
            "[QoS 0] msg_id=%d from %s — delivering immediately.", packet.msg_id, packet.addr
        )
        self._on_deliver(packet)
        with self._lock:
            self._total_delivered += 1

    def _handle_qos1(self, packet: PublishPacket) -> None:
        """QoS 1: At-least-once. Send PUBACK, then deliver."""
        self._send_puback(packet.addr, packet.msg_id)
        logger.info(
            "[QoS 1] PUBACK sent for msg_id=%d to %s.", packet.msg_id, packet.addr
        )
        self._on_deliver(packet)
        with self._lock:
            self._total_delivered += 1

    def _handle_qos2_publish(self, packet: PublishPacket) -> None:
        """
        QoS 2 step 1: PUBLISH received.
        Store the packet and send PUBREC; do NOT deliver yet.
        """
        msg_id = packet.msg_id
        addr   = packet.addr

        with self._lock:
            if msg_id in self._qos2_pending:
                logger.warning(
                    "[QoS 2] Duplicate PUBLISH msg_id=%d from %s — resending PUBREC.",
                    msg_id, addr,
                )
                self._send_pubrec(addr, msg_id)
                return

            self._qos2_pending[msg_id] = QoS2Record(
                msg_id=msg_id,
                addr=addr,
                packet=packet,
            )

        self._send_pubrec(addr, msg_id)
        logger.info(
            "[QoS 2] PUBREC sent for msg_id=%d to %s — awaiting PUBREL.", msg_id, addr
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Wire-format senders
    # ──────────────────────────────────────────────────────────────────────────

    def _send_puback(self, addr: Addr, msg_id: int) -> None:
        """Send a PUBACK packet (QoS 1 ack)."""
        packet = struct.pack("<BBH", 4, MSG_PUBACK, msg_id)
        self._sock.sendto(packet, addr)
        with self._lock:
            self._total_puback_sent += 1

    def _send_pubrec(self, addr: Addr, msg_id: int) -> None:
        """Send a PUBREC packet (QoS 2, step 1 ack)."""
        packet = struct.pack("<BBH", 4, MSG_PUBREC, msg_id)
        self._sock.sendto(packet, addr)
        with self._lock:
            self._total_pubrec_sent += 1

    def _send_pubcomp(self, addr: Addr, msg_id: int) -> None:
        """Send a PUBCOMP packet (QoS 2, step 3 completion)."""
        packet = struct.pack("<BBH", 4, MSG_PUBCOMP, msg_id)
        self._sock.sendto(packet, addr)
        with self._lock:
            self._total_pubcomp_sent += 1

    # ──────────────────────────────────────────────────────────────────────────
    # Background: QoS 2 garbage collector
    # ──────────────────────────────────────────────────────────────────────────

    def _gc_loop(self) -> None:
        """
        Periodically evict in-flight QoS 2 records that have been waiting
        longer than QOS2_TIMEOUT_SECONDS (client likely crashed or went to
        deep sleep without sending PUBREL).
        """
        while True:
            time.sleep(QOS2_TIMEOUT_SECONDS // 2 or 1)
            now = time.time()
            expired: list[int] = []

            with self._lock:
                for msg_id, rec in self._qos2_pending.items():
                    if now - rec.created_at > QOS2_TIMEOUT_SECONDS:
                        expired.append(msg_id)

            for msg_id in expired:
                record: Optional[QoS2Record] = None
                with self._lock:
                    record = self._qos2_pending.pop(msg_id, None)
                    if record is not None:
                        self._total_expired += 1

                if record is not None:
                    logger.warning(
                        "[QoS 2 GC] Expired in-flight record msg_id=%d from %s — "
                        "client may be asleep or unreachable.",
                        msg_id, record.addr,
                    )
