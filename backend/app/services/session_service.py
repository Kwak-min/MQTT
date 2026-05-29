"""
backend/app/services/session_service.py
─────────────────────────────────────────────────────────────────────────────
Thread-safe in-memory session table.

Tracks the lifecycle of every ESP32 device that communicates with the server:
  CONNECT     → create or re-activate session
  DISCONNECT  → mark session as ASLEEP (node returned to deep sleep)

This service is intentionally separate from the socket layer.
The socket layer calls these methods via the callbacks injected in main.py.

Future extension: swap the in-memory dict for a DB-backed store by
replacing the internal dict with an ORM session without touching callers.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import csv
import logging
import os
import threading
import time
from typing import Dict, List, Optional

from app.models.packet import ConnectPacket, DisconnectPacket
from app.models.session import Session, SessionStatus
from config import SESSION_LOG_CSV, LOG_DIR

logger = logging.getLogger(__name__)


class SessionService:
    """
    In-memory session registry with CSV-backed event log.

    Thread-safe for concurrent access from multiple listener threads.
    """

    def __init__(self) -> None:
        self._lock: threading.Lock = threading.Lock()
        self._sessions: Dict[str, Session] = {}

        # Ensure log directory exists and write CSV header if new file
        os.makedirs(LOG_DIR, exist_ok=True)
        self._init_csv()

    # ──────────────────────────────────────────────────────────────────────────
    # Public write interface  (called by GingerbreadListener callbacks in main.py)
    # ──────────────────────────────────────────────────────────────────────────

    def on_connect(self, packet: ConnectPacket) -> None:
        """
        Create a new session or re-activate an existing one.
        Called when a CONNECT packet is received.
        """
        client_id = packet.client_id
        addr      = packet.addr

        with self._lock:
            existing = self._sessions.get(client_id)
            if existing:
                # Node woke up again — refresh
                existing.addr   = addr
                existing.status = SessionStatus.ACTIVE
                existing.touch()
                logger.info(
                    "[Session] Re-activated session for client_id='%s' from %s",
                    client_id, addr,
                )
            else:
                session = Session(client_id=client_id, addr=addr)
                self._sessions[client_id] = session
                logger.info(
                    "[Session] New session created for client_id='%s' from %s",
                    client_id, addr,
                )
                existing = session

        self._log_event(existing, event="CONNECT")

    def on_disconnect(self, packet: DisconnectPacket) -> None:
        """
        Mark the session whose address matches the sender as ASLEEP.
        Called when a DISCONNECT packet is received.
        """
        addr = packet.addr
        session: Optional[Session] = None

        with self._lock:
            for sess in self._sessions.values():
                if sess.addr == addr:
                    sess.status = SessionStatus.ASLEEP
                    sess.touch()
                    session = sess
                    break

        if session:
            logger.info(
                "[Session] client_id='%s' went ASLEEP from %s",
                session.client_id, addr,
            )
            self._log_event(session, event="DISCONNECT")
        else:
            logger.warning(
                "[Session] DISCONNECT from unknown addr %s — no matching session.", addr
            )

    def increment_packet_count(self, addr) -> None:
        """Increment PUBLISH counter for the session that owns addr."""
        with self._lock:
            for sess in self._sessions.values():
                if sess.addr == addr:
                    sess.packet_count += 1
                    sess.touch()
                    break

    # ──────────────────────────────────────────────────────────────────────────
    # Public read interface  (called by REST controller layer)
    # ──────────────────────────────────────────────────────────────────────────

    def get_all(self) -> List[dict]:
        """Return a snapshot of all sessions as dicts (for REST API)."""
        with self._lock:
            return [s.to_dict() for s in self._sessions.values()]

    def get_by_client_id(self, client_id: str) -> Optional[dict]:
        """Return a single session dict by client_id, or None."""
        with self._lock:
            sess = self._sessions.get(client_id)
            return sess.to_dict() if sess else None

    def get_active_count(self) -> int:
        """Return the number of sessions currently in ACTIVE state."""
        with self._lock:
            return sum(
                1 for s in self._sessions.values()
                if s.status == SessionStatus.ACTIVE
            )

    def get_asleep_count(self) -> int:
        """Return the number of sessions currently in ASLEEP state."""
        with self._lock:
            return sum(
                1 for s in self._sessions.values()
                if s.status == SessionStatus.ASLEEP
            )

    def get_stats(self) -> dict:
        """Return a summary dict for the /api/sessions/stats endpoint."""
        with self._lock:
            total   = len(self._sessions)
            active  = sum(1 for s in self._sessions.values() if s.status == SessionStatus.ACTIVE)
            asleep  = sum(1 for s in self._sessions.values() if s.status == SessionStatus.ASLEEP)
            timeout = sum(1 for s in self._sessions.values() if s.status == SessionStatus.TIMED_OUT)
        return {
            "total":      total,
            "active":     active,
            "asleep":     asleep,
            "timed_out":  timeout,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # CSV event logging
    # ──────────────────────────────────────────────────────────────────────────

    _CSV_FIELDNAMES = [
        "timestamp", "event", "client_id", "addr_ip", "addr_port", "status"
    ]

    def _init_csv(self) -> None:
        if not os.path.exists(SESSION_LOG_CSV):
            with open(SESSION_LOG_CSV, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=self._CSV_FIELDNAMES)
                writer.writeheader()

    def _log_event(self, session: Session, event: str) -> None:
        row = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "event":     event,
            "client_id": session.client_id,
            "addr_ip":   session.addr[0],
            "addr_port": session.addr[1],
            "status":    session.status.name,
        }
        try:
            with open(SESSION_LOG_CSV, "a", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=self._CSV_FIELDNAMES).writerow(row)
        except OSError as exc:
            logger.error("[Session] Failed to write CSV log: %s", exc)
