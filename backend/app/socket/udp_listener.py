"""
backend/app/socket/udp_listener.py
─────────────────────────────────────────────────────────────────────────────
Background UDP Socket Listener.

Each UDPListener instance binds a single UDP socket on a designated port
and spins a daemon thread that continuously calls recvfrom().

Two instances are created in main.py:
  • GingerbreadListener  — port 5000, Node B packets (binary Gingerbread)
  • PowerListener        — port 6000, ESP32-C3 JSON power streaming

Dependency wiring (injected by main.py, NOT imported here):
  ┌────────────────────────────────────────────────────────────┐
  │  UDPListener                                               │
  │    ↓ raw bytes                                             │
  │  packet_parser  →  typed Packet (with arrival timestamp)   │
  │    ↓                                                       │
  │  QoSHandler (Gingerbread only)  or  direct dispatch        │
  │    ↓ confirmed PublishPacket / PowerPacket                 │
  │  SessionService  +  TelemetryService                       │
  └────────────────────────────────────────────────────────────┘

Metrics exposed per listener:
  .recv_count   — total datagrams received since start()
  .error_count  — total dispatch/parse errors since start()
  .is_alive     — True while the listener thread is running
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
# Base listener
# ──────────────────────────────────────────────────────────────────────────────

class UDPListener:
    """
    Generic UDP listener that runs on a daemon thread.

    Subclass and override _dispatch() to handle decoded packets.

    Metrics
    -------
    recv_count   : int — total datagrams successfully passed to _dispatch()
    error_count  : int — total datagrams that raised an exception in _dispatch()
    is_alive     : bool — True while the listener thread is running
    """

    def __init__(self, host: str, port: int, name: str) -> None:
        self._host = host
        self._port = port
        self._name = name
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # ── Metrics ──────────────────────────────────────────────────────────
        self._recv_count:  int = 0
        self._error_count: int = 0
        self._metrics_lock = threading.Lock()

        # ── Socket ───────────────────────────────────────────────────────────
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self._host, self._port))
        # Allow blocking recvfrom() to respect stop_event by timing out regularly
        self._sock.settimeout(1.0)

        logger.info("[%s] Bound to UDP %s:%d", self._name, self._host, self._port)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Spawn the daemon listener thread."""
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name=f"udp-{self._name.lower()}",
        )
        self._thread.start()
        logger.info("[%s] Listener thread started.", self._name)

    def stop(self) -> None:
        """Signal the listener loop to exit and close the socket."""
        self._stop_event.set()
        try:
            self._sock.close()
        except OSError:
            pass
        if self._thread:
            self._thread.join(timeout=3)
        logger.info("[%s] Listener stopped.", self._name)

    # ── Metrics properties ────────────────────────────────────────────────────

    @property
    def recv_count(self) -> int:
        """Total datagrams dispatched since start()."""
        with self._metrics_lock:
            return self._recv_count

    @property
    def error_count(self) -> int:
        """Total dispatch errors since start()."""
        with self._metrics_lock:
            return self._error_count

    @property
    def is_alive(self) -> bool:
        """True while the listener thread is running."""
        return self._thread is not None and self._thread.is_alive()

    def get_metrics(self) -> dict:
        """Return a metrics snapshot as a plain dict."""
        with self._metrics_lock:
            return {
                "name":        self._name,
                "host":        self._host,
                "port":        self._port,
                "is_alive":    self.is_alive,
                "recv_count":  self._recv_count,
                "error_count": self._error_count,
            }

    # ── Internal loop ─────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        """Blocking receive loop — runs on the daemon thread."""
        while not self._stop_event.is_set():
            try:
                raw, addr = self._sock.recvfrom(UDP_BUFFER_SIZE)
            except socket.timeout:
                continue
            except OSError:
                # Socket was closed by stop()
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
        """Override in subclasses to process a received datagram."""
        raise NotImplementedError


# ──────────────────────────────────────────────────────────────────────────────
# Gingerbread Listener  (Node B, port 5000)
# ──────────────────────────────────────────────────────────────────────────────

class GingerbreadListener(UDPListener):
    """
    Listens on port 5000 for Node B (ESP32-S3) Gingerbread UDP packets.

    Injects all dependencies through the constructor.
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

        # QoSHandler is created here so it owns the same socket reference
        # needed to send PUBACK/PUBREC/PUBCOMP back to the client.
        self._qos_handler = QoSHandler(sock=self._sock, on_deliver=on_deliver)

    @property
    def qos_handler(self) -> QoSHandler:
        """Expose the QoSHandler for diagnostics / testing."""
        return self._qos_handler

    def _dispatch(self, raw: bytes, addr) -> None:
        """Parse raw bytes and route to session or QoS handler."""
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
# Power MCU Listener  (ESP32-C3, port 6000)
# ──────────────────────────────────────────────────────────────────────────────

class PowerListener(UDPListener):
    """
    Listens on port 6000 for ESP32-C3 Power MCU JSON power streaming packets.
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
