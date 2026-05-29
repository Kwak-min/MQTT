"""
backend/main.py
─────────────────────────────────────────────────────────────────────────────
Application entry point.

Wires together all layers of the backend:
  1. Instantiate services (SessionService, TelemetryService)
  2. Resolve callback functions that connect socket ↔ service layers
  3. Instantiate and start UDP listener threads
  4. Start the Flask REST API server on a daemon thread
  5. Block the main thread until KeyboardInterrupt

Run from the backend/ directory:
  python main.py

Or from the project root:
  python -m backend
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
import os
import sys
import threading

# ── Make sure `backend/` is on sys.path so sub-packages resolve correctly ──
# This lets you run `python main.py` from inside backend/ without installing.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# ─────────────────────────────────────────────────────────────────────────────
# Configure logging before importing anything else
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("main")

# ─────────────────────────────────────────────────────────────────────────────
# Imports (after sys.path fix)
# ─────────────────────────────────────────────────────────────────────────────
from config import UDP_HOST, GINGERBREAD_PORT, POWER_PORT

from app.services.session_service   import SessionService
from app.services.telemetry_service import TelemetryService
from app.socket.udp_listener        import GingerbreadListener, PowerListener
from app.models.packet              import ConnectPacket, DisconnectPacket, PublishPacket, PowerPacket


def main() -> None:
    logger.info("=" * 60)
    logger.info("  IoT Gateway Backend  —  starting up")
    logger.info("=" * 60)

    # ──────────────────────────────────────────────────────────────────────────
    # 1. Services
    # ──────────────────────────────────────────────────────────────────────────
    session_svc   = SessionService()
    telemetry_svc = TelemetryService()
    logger.info("[Main] Services initialised.")

    # ──────────────────────────────────────────────────────────────────────────
    # 2. Callback closures (socket layer → service layer bridge)
    # ──────────────────────────────────────────────────────────────────────────

    def on_connect(packet: ConnectPacket) -> None:
        session_svc.on_connect(packet)

    def on_disconnect(packet: DisconnectPacket) -> None:
        session_svc.on_disconnect(packet)

    def on_env_deliver(packet: PublishPacket) -> None:
        """
        Called by QoSHandler once a PUBLISH is confirmed (all QoS levels).
        Resolves the client_id from the session table before logging.
        """
        # Resolve client_id from session table by sender address
        client_id = None
        for sess in session_svc.get_all():
            if sess["addr_ip"] == packet.addr[0] and sess["addr_port"] == packet.addr[1]:
                client_id = sess["client_id"]
                break

        session_svc.increment_packet_count(packet.addr)
        telemetry_svc.record_env_telemetry(packet, client_id=client_id)

    def on_power(packet: PowerPacket) -> None:
        """Called for every Power MCU streaming packet."""
        telemetry_svc.record_power_telemetry(packet)

    # ──────────────────────────────────────────────────────────────────────────
    # 3. UDP Listeners
    # ──────────────────────────────────────────────────────────────────────────
    gingerbread_listener = GingerbreadListener(
        host=UDP_HOST,
        port=GINGERBREAD_PORT,
        on_connect=on_connect,
        on_disconnect=on_disconnect,
        on_deliver=on_env_deliver,
    )

    power_listener = PowerListener(
        host=UDP_HOST,
        port=POWER_PORT,
        on_power=on_power,
    )

    gingerbread_listener.start()
    power_listener.start()

    logger.info("[Main] Listeners started.")
    logger.info("[Main]   → Gingerbread  (Node B)    : UDP %s:%d", UDP_HOST, GINGERBREAD_PORT)
    logger.info("[Main]   → Power MCU    (ESP32-C3)  : UDP %s:%d", UDP_HOST, POWER_PORT)

    # ──────────────────────────────────────────────────────────────────────────
    # 4. Flask REST API  (daemon thread — optional, safe to comment out)
    # ──────────────────────────────────────────────────────────────────────────
    _start_flask_api(
        session_svc=session_svc,
        telemetry_svc=telemetry_svc,
        gingerbread_listener=gingerbread_listener,
        power_listener=power_listener,
    )

    # ──────────────────────────────────────────────────────────────────────────
    # 5. Keep main thread alive
    # ──────────────────────────────────────────────────────────────────────────
    logger.info("[Main] Backend running. Press Ctrl+C to stop.")
    shutdown_event = threading.Event()
    try:
        shutdown_event.wait()   # blocks until KeyboardInterrupt
    except KeyboardInterrupt:
        logger.info("\n[Main] Shutdown signal received.")
    finally:
        gingerbread_listener.stop()
        power_listener.stop()
        logger.info("[Main] All listeners stopped. Goodbye.")


def _start_flask_api(
    session_svc,
    telemetry_svc,
    gingerbread_listener=None,
    power_listener=None,
) -> None:
    """
    Start the Flask REST API on a background daemon thread.
    Bound to 0.0.0.0:8080 so the React/Vue dev server can reach it.

    Registers both blueprints:
      /api/telemetry/...  — environmental + power data + SSE stream
      /api/sessions/...   — device session table
      /api/diagnostics    — QoS counters + listener health
      /api/health         — liveness probe

    If Flask is not installed, this function logs a warning and returns
    gracefully — the UDP gateway continues to function without the REST API.
    """
    try:
        from flask import Flask
        from flask_cors import CORS
        from app.controllers.telemetry_controller import create_blueprint as create_telemetry_bp
        from app.controllers.session_controller   import create_blueprint as create_session_bp
    except ImportError as exc:
        logger.warning(
            "[Main] Flask dependency missing — REST API disabled. "
            "Run:  pip install flask flask-cors\n  Detail: %s", exc
        )
        return

    flask_app = Flask(__name__)
    # Allow React/Vue dev server (localhost:3000) during development
    CORS(flask_app, resources={r"/api/*": {"origins": "*"}})

    # Register telemetry blueprint (includes /api/telemetry/*, /api/diagnostics, /api/health)
    telemetry_bp = create_telemetry_bp(
        telemetry_svc=telemetry_svc,
        gingerbread_listener=gingerbread_listener,
        power_listener=power_listener,
    )
    flask_app.register_blueprint(telemetry_bp, url_prefix="/api")

    # Register session blueprint (/api/sessions/*)
    session_bp = create_session_bp(session_svc=session_svc)
    flask_app.register_blueprint(session_bp, url_prefix="/api")

    api_thread = threading.Thread(
        target=lambda: flask_app.run(
            host="0.0.0.0",
            port=8080,
            debug=False,        # must be False in a non-main thread
            use_reloader=False,
            threaded=True,      # required for SSE — each SSE client gets its own thread
        ),
        daemon=True,
        name="flask-api",
    )
    api_thread.start()

    logger.info("[Main]   → REST API (Flask)          : http://0.0.0.0:8080/api")
    logger.info("[Main]     Endpoints:")
    logger.info("[Main]       GET  /api/health")
    logger.info("[Main]       GET  /api/diagnostics")
    logger.info("[Main]       GET  /api/telemetry/env")
    logger.info("[Main]       GET  /api/telemetry/power")
    logger.info("[Main]       GET  /api/telemetry/stream  (SSE)")
    logger.info("[Main]       GET  /api/sessions")
    logger.info("[Main]       GET  /api/sessions/stats")
    logger.info("[Main]       GET  /api/sessions/<client_id>")


if __name__ == "__main__":
    main()
