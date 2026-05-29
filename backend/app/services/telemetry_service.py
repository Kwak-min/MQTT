"""
backend/app/services/telemetry_service.py
─────────────────────────────────────────────────────────────────────────────
Telemetry Service — decouples data ingestion from storage and querying.

Receives confirmed, decoded payload dicts from the socket/QoS layer and:
  1. Writes them to persistent storage  (currently: CSV files)
  2. Fans out real-time data to SSE subscriber queues (for GET /api/telemetry/stream)
  3. Provides read methods used by the REST controller layer

Storage swap guide
──────────────────
  Replace _write_env_row() and _write_power_row() with DB insert calls.
  The public interface stays identical — no other file needs to change.

  Example (InfluxDB):
    def _write_env_row(self, row: dict) -> None:
        point = Point("environment") \
            .tag("client_id", row["client_id"]) \
            .field("temp", row["temp"]) ...
        influx_client.write(point)

Two data streams:
  1. Environmental telemetry  (Node B PUBLISH payloads)
       Keys: temp, hum, gas   (+ server-side: timestamp, client_id, msg_id, qos, topic_id)

  2. Power telemetry  (ESP32-C3 Power MCU JSON stream)
       Keys: node, current_mA, voltage_V, power_mW  (+ server-side: timestamp)
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import csv
import logging
import os
import queue
import threading
import time
from typing import List, Optional

from app.models.packet import PowerPacket, PublishPacket
from config import LOG_DIR, POWER_CSV, TELEMETRY_CSV

logger = logging.getLogger(__name__)


class TelemetryService:
    """
    Handles persistence and real-time streaming of all telemetry data.

    Thread-safe: both listener threads (port 5000 and 6000) may call this
    service concurrently — each stream uses its own lock.

    SSE Fan-out
    ───────────
    Any number of HTTP clients can subscribe to the live environmental
    telemetry stream via subscribe_env() / unsubscribe_env().
    Each subscriber receives a threading.Queue; when new env data arrives,
    every subscriber queue gets the row dict pushed in.
    """

    # ── CSV field definitions (single source of truth) ────────────────────────

    _ENV_FIELDS = [
        "timestamp", "client_id", "msg_id", "qos", "topic_id",
        "addr_ip", "addr_port", "temp", "hum", "gas", "power", "raw_payload",
    ]

    _POWER_FIELDS = [
        "timestamp", "node", "addr_ip", "addr_port",
        "current_mA", "voltage_V", "power_mW",
    ]

    def __init__(self) -> None:
        os.makedirs(LOG_DIR, exist_ok=True)
        self._env_lock   = threading.Lock()
        self._power_lock = threading.Lock()
        self._init_csv_files()

        # SSE subscriber list — protected by its own lock
        self._sse_lock:        threading.Lock      = threading.Lock()
        self._sse_subscribers: list[queue.Queue]   = []

    # ──────────────────────────────────────────────────────────────────────────
    # Public write interface  (called by callbacks in main.py)
    # ──────────────────────────────────────────────────────────────────────────

    def record_env_telemetry(
        self,
        packet: PublishPacket,
        client_id: Optional[str] = None,
    ) -> None:
        """
        Persist an environmental PUBLISH packet to the telemetry CSV
        and fan-out to any live SSE subscribers.

        Parameters
        ----------
        packet    : Confirmed PublishPacket (QoS handshake complete).
        client_id : Resolved device ID from the session table (may be None
                    if the session was not found, e.g. node crashed before CONNECT).
        """
        payload = packet.payload or {}

        row = {
            "timestamp":  time.strftime("%Y-%m-%dT%H:%M:%S"),
            "client_id":  client_id or "unknown",
            "msg_id":     packet.msg_id,
            "qos":        packet.qos,
            "topic_id":   packet.topic_id,
            "addr_ip":    packet.addr[0],
            "addr_port":  packet.addr[1],
            # BME280 / environmental sensor fields (None if key absent)
            "temp":       payload.get("temp"),
            "hum":        payload.get("hum"),
            "gas":        payload.get("gas"),
            # Some firmware versions include a "power" field in the env payload
            "power":      payload.get("power"),
            # Raw payload string preserved for debugging
            "raw_payload": packet.payload_raw,
        }

        with self._env_lock:
            self._write_env_row(row)

        # Fan-out to SSE clients (non-blocking)
        self._fanout_env(row)

        logger.info(
            "[Telemetry] ENV recorded — client='%s' msg_id=%d temp=%s hum=%s gas=%s",
            row["client_id"], row["msg_id"], row["temp"], row["hum"], row["gas"],
        )

    def record_power_telemetry(self, packet: PowerPacket) -> None:
        """
        Persist a Power MCU streaming packet to the power CSV.

        Parameters
        ----------
        packet : Decoded PowerPacket from the ESP32-C3.
        """
        row = {
            "timestamp":  time.strftime("%Y-%m-%dT%H:%M:%S"),
            "node":       packet.node,
            "addr_ip":    packet.addr[0],
            "addr_port":  packet.addr[1],
            "current_mA": packet.current_mA,
            "voltage_V":  packet.voltage_V,
            "power_mW":   packet.power_mW,
        }

        with self._power_lock:
            self._write_power_row(row)

        logger.debug(
            "[Telemetry] POWER recorded — Node=%s  %.2f mA  %.3f V  %.2f mW",
            row["node"], row["current_mA"], row["voltage_V"], row["power_mW"],
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Public read interface  (called by REST controller layer)
    # ──────────────────────────────────────────────────────────────────────────

    def get_recent_env(
        self,
        limit: int = 50,
        client_id: Optional[str] = None,
    ) -> List[dict]:
        """
        Return the last `limit` environmental telemetry rows from CSV.

        Parameters
        ----------
        limit     : Maximum number of rows (capped at 1000).
        client_id : Optional filter by device client_id.
        """
        limit = max(1, min(limit, 1000))
        rows  = _read_csv_tail(TELEMETRY_CSV, limit * 2 if client_id else limit)
        if client_id:
            rows = [r for r in rows if r.get("client_id") == client_id]
        return rows[-limit:]

    def get_recent_power(
        self,
        limit: int = 100,
        node: Optional[str] = None,
    ) -> List[dict]:
        """
        Return the last `limit` power telemetry rows from CSV.

        Parameters
        ----------
        limit : Maximum number of rows (capped at 1000).
        node  : Optional filter — "A" or "B".
        """
        limit = max(1, min(limit, 1000))
        rows  = _read_csv_tail(POWER_CSV, limit * 2 if node else limit)
        if node:
            rows = [r for r in rows if r.get("node") == node.upper()]
        return rows[-limit:]

    def get_stats(self) -> dict:
        """
        Return a summary of stored telemetry row counts per CSV file.
        Used by the /api/diagnostics endpoint.
        """
        return {
            "env_rows":   _count_csv_rows(TELEMETRY_CSV),
            "power_rows": _count_csv_rows(POWER_CSV),
        }

    # ──────────────────────────────────────────────────────────────────────────
    # SSE subscription management
    # ──────────────────────────────────────────────────────────────────────────

    def subscribe_env(self, maxsize: int = 100) -> queue.Queue:
        """
        Register a new SSE subscriber for live environmental telemetry.

        Returns a Queue that will receive env-row dicts as they arrive.
        Call unsubscribe_env(q) when the client disconnects.
        """
        q: queue.Queue = queue.Queue(maxsize=maxsize)
        with self._sse_lock:
            self._sse_subscribers.append(q)
        logger.debug("[Telemetry] SSE subscriber added. Total: %d", len(self._sse_subscribers))
        return q

    def unsubscribe_env(self, q: queue.Queue) -> None:
        """Remove a previously subscribed SSE queue."""
        with self._sse_lock:
            try:
                self._sse_subscribers.remove(q)
                logger.debug(
                    "[Telemetry] SSE subscriber removed. Total: %d",
                    len(self._sse_subscribers),
                )
            except ValueError:
                pass  # already removed

    def _fanout_env(self, row: dict) -> None:
        """Push a row to all SSE subscriber queues (non-blocking; drops if full)."""
        with self._sse_lock:
            dead: list[queue.Queue] = []
            for q in self._sse_subscribers:
                try:
                    q.put_nowait(row)
                except queue.Full:
                    # Client is too slow — drop and mark for removal
                    dead.append(q)
            for q in dead:
                self._sse_subscribers.remove(q)
                logger.warning("[Telemetry] SSE subscriber queue full — removed.")

    # ──────────────────────────────────────────────────────────────────────────
    # Storage adapters  (swap out here for DB without touching callers)
    # ──────────────────────────────────────────────────────────────────────────

    def _write_env_row(self, row: dict) -> None:
        """Write one environmental telemetry row to CSV. (Lock held by caller.)"""
        try:
            with open(TELEMETRY_CSV, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=self._ENV_FIELDS, extrasaction="ignore")
                writer.writerow(row)
        except OSError as exc:
            logger.error("[Telemetry] Failed to write env CSV: %s", exc)

    def _write_power_row(self, row: dict) -> None:
        """Write one power telemetry row to CSV. (Lock held by caller.)"""
        try:
            with open(POWER_CSV, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=self._POWER_FIELDS, extrasaction="ignore")
                writer.writerow(row)
        except OSError as exc:
            logger.error("[Telemetry] Failed to write power CSV: %s", exc)

    # ──────────────────────────────────────────────────────────────────────────
    # Initialise CSV headers
    # ──────────────────────────────────────────────────────────────────────────

    def _init_csv_files(self) -> None:
        """Write CSV headers for any files that don't yet exist."""
        for path, fields in [
            (TELEMETRY_CSV, self._ENV_FIELDS),
            (POWER_CSV,     self._POWER_FIELDS),
        ]:
            if not os.path.exists(path):
                try:
                    with open(path, "w", newline="", encoding="utf-8") as f:
                        csv.DictWriter(f, fieldnames=fields).writeheader()
                    logger.info("[Telemetry] Created log file: %s", path)
                except OSError as exc:
                    logger.error("[Telemetry] Failed to create %s: %s", path, exc)


# ──────────────────────────────────────────────────────────────────────────────
# Module-level helpers (not part of the class — pure utility)
# ──────────────────────────────────────────────────────────────────────────────

def _read_csv_tail(path: str, n: int) -> list[dict]:
    """
    Read the last N rows from a CSV file without loading the entire file.
    Returns rows as a list of dicts (field → value).
    """
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", newline="", encoding="utf-8") as f:
            reader = list(csv.DictReader(f))
            return reader[-n:]
    except OSError as exc:
        logger.error("[Telemetry] Failed to read CSV %s: %s", path, exc)
        return []


def _count_csv_rows(path: str) -> int:
    """Count data rows in a CSV file (excludes header). Returns 0 if not found."""
    if not os.path.exists(path):
        return 0
    try:
        with open(path, "r", newline="", encoding="utf-8") as f:
            # subtract 1 for the header row
            return max(0, sum(1 for _ in f) - 1)
    except OSError:
        return 0
