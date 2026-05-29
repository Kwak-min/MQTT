"""
backend/app/controllers/telemetry_controller.py
─────────────────────────────────────────────────────────────────────────────
Web Controller — Telemetry REST API + Server-Sent Events.

All data reads are delegated to TelemetryService — this layer is a thin
HTTP adapter and must NOT directly touch CSV files or sockets.

Endpoints
─────────
  GET /api/telemetry/env?limit=50&client_id=<id>
      Last N environmental telemetry rows (CSV-backed, paginated).

  GET /api/telemetry/power?limit=100&node=A
      Last N power telemetry rows (CSV-backed, paginated).

  GET /api/telemetry/latest/env
      Single latest environmental reading from in-memory snapshot.
      Always returns all BME680 fields (gas, gas_valid, etc.) with
      0.0 defaults even before the first packet arrives.

  GET /api/telemetry/latest/power?node=A
      Latest power reading(s) from in-memory snapshot (no disk I/O).

  GET /api/telemetry/stream
      Server-Sent Events — pushed in real-time whenever env data arrives.
      The frontend can open a persistent EventSource connection here.

  GET /api/diagnostics
      QoS protocol counters, listener health, telemetry row counts.

  GET /api/health
      Simple liveness probe.

Framework: Flask (swap for FastAPI by replacing @bp.route with @router.get).

CORS
────
flask-cors is applied in main.py so the React/Vue dev server (localhost:3000)
can reach all these endpoints during development.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING

from flask import Blueprint, Response, jsonify, request, stream_with_context

if TYPE_CHECKING:
    from app.services.session_service   import SessionService
    from app.services.telemetry_service import TelemetryService
    from app.socket.udp_listener        import GingerbreadListener, PowerListener
    from app.socket.qos_handler         import QoSHandler

logger = logging.getLogger(__name__)


def create_blueprint(
    telemetry_svc: "TelemetryService",
    gingerbread_listener: "GingerbreadListener | None" = None,
    power_listener:       "PowerListener | None"       = None,
) -> Blueprint:
    """
    Factory function that creates and returns the telemetry API Blueprint.

    Parameters
    ----------
    telemetry_svc          : TelemetryService instance (required).
    gingerbread_listener   : GingerbreadListener — exposes QoS diagnostics.
                             Pass None to disable the diagnostics endpoint.
    power_listener         : PowerListener — exposes power listener metrics.
                             Pass None to skip power listener metrics.
    """
    bp = Blueprint("telemetry", __name__)

    # ──────────────────────────────────────────────────────────────────────────
    # GET /api/telemetry/env?limit=50&client_id=<id>
    # ──────────────────────────────────────────────────────────────────────────

    @bp.route("/telemetry/env", methods=["GET"])
    def get_env_telemetry():
        """
        Return the last N environmental telemetry rows.

        Query parameters:
          limit     (int, default 50)  : Maximum number of rows to return.
          client_id (str, optional)    : Filter by device client_id.

        Response shape:
          { "count": 12, "data": [ { "timestamp": "...", "temp": 24.5, ... }, ... ] }
        """
        limit     = _parse_limit(request.args.get("limit", 50))
        client_id = request.args.get("client_id", None)
        rows      = telemetry_svc.get_recent_env(limit=limit, client_id=client_id)
        return jsonify({"count": len(rows), "data": rows})

    # ──────────────────────────────────────────────────────────────────────────
    # GET /api/telemetry/power?limit=100&node=A
    # ──────────────────────────────────────────────────────────────────────────

    @bp.route("/telemetry/power", methods=["GET"])
    def get_power_telemetry():
        """
        Return the last N power telemetry rows.

        Query parameters:
          limit (int, default 100) : Maximum rows to return.
          node  (str, optional)    : Filter by node label ("A" or "B").

        Response shape:
          { "count": 50, "data": [ { "node": "A", "current_mA": 12.5, ... }, ... ] }
        """
        limit = _parse_limit(request.args.get("limit", 100))
        node  = request.args.get("node", None)
        rows  = telemetry_svc.get_recent_power(limit=limit, node=node)
        return jsonify({"count": len(rows), "data": rows})

    # ──────────────────────────────────────────────────────────────────────────
    # GET /api/telemetry/latest/env   — instant in-memory read
    # ──────────────────────────────────────────────────────────────────────────

    @bp.route("/telemetry/latest/env", methods=["GET"])
    def get_latest_env():
        """
        Return the latest environmental reading from the in-memory snapshot.

        This endpoint reads self.telemetry_data directly — no CSV I/O.
        All BME680 fields (temp, hum, gas, gas_valid, power) are always
        present, defaulting to 0.0 / False before the first packet arrives.

        Response shape:
          {
            "timestamp": "2026-05-29T17:00:00",
            "client_id": "nodeB_001",
            "temp": 24.5,
            "hum":  61.2,
            "gas":  124500.0,
            "gas_valid": true,
            "power": null,
            "packet_count": 42
          }
        """
        snapshot = telemetry_svc.get_latest_env()
        return jsonify(snapshot)

    # ──────────────────────────────────────────────────────────────────────────
    # GET /api/telemetry/latest/power?node=A  — instant in-memory read
    # ──────────────────────────────────────────────────────────────────────────

    @bp.route("/telemetry/latest/power", methods=["GET"])
    def get_latest_power():
        """
        Return the latest power reading(s) from the in-memory snapshot.

        This endpoint reads self.power_data directly — no CSV I/O.
        Both nodes (A and B) default to 0.0 before any packets arrive.

        Query parameters:
          node (str, optional) : "A" or "B" — return that node only.
                                 Omit to return both nodes.

        Response shape (no node filter):
          {
            "A": { "current_mA": 12.5, "voltage_V": 4.98, ... },
            "B": { "current_mA": 8.1,  "voltage_V": 4.97, ... }
          }

        Response shape (node=A):
          { "node": "A", "current_mA": 12.5, "voltage_V": 4.98, ... }
        """
        node     = request.args.get("node", None)
        snapshot = telemetry_svc.get_latest_power(node=node)
        return jsonify(snapshot)

    # ──────────────────────────────────────────────────────────────────────────
    # GET /api/telemetry/stream  — Server-Sent Events
    # ──────────────────────────────────────────────────────────────────────────

    @bp.route("/telemetry/stream", methods=["GET"])
    def telemetry_stream():
        """
        Server-Sent Events stream — pushed in real-time whenever a new
        environmental telemetry row is recorded.

        Frontend usage (JavaScript):
          const es = new EventSource('/api/telemetry/stream');
          es.onmessage = (e) => {
              const row = JSON.parse(e.data);
              console.log(row.temp, row.hum, row.gas);
          };

        Each event is formatted as:
          data: {"timestamp": "...", "temp": 24.5, "hum": 60.1, ...}\n\n
        """
        q = telemetry_svc.subscribe_env()

        def generate():
            # Send an initial comment to keep the connection alive immediately
            yield ": connected\n\n"
            try:
                while True:
                    try:
                        row = q.get(timeout=30)   # 30-s heartbeat
                        yield f"data: {json.dumps(row)}\n\n"
                    except Exception:
                        # Timeout — send a keep-alive comment
                        yield ": heartbeat\n\n"
            except GeneratorExit:
                pass
            finally:
                telemetry_svc.unsubscribe_env(q)

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control":   "no-cache",
                "X-Accel-Buffering": "no",    # disable nginx buffering
            },
        )

    # ──────────────────────────────────────────────────────────────────────────
    # GET /api/diagnostics
    # ──────────────────────────────────────────────────────────────────────────

    @bp.route("/diagnostics", methods=["GET"])
    def diagnostics():
        """
        Aggregate diagnostics from all layers.

        Response shape:
          {
            "server_time": "2026-05-29T17:00:00",
            "listeners": {
              "gingerbread": { "is_alive": true, "recv_count": 42, ... },
              "power":       { "is_alive": true, "recv_count": 120, ... }
            },
            "qos": {
              "pending_qos2_count": 0,
              "total_delivered":    42,
              ...
            },
            "telemetry": {
              "env_rows":   500,
              "power_rows": 1200
            }
          }
        """
        payload: dict = {
            "server_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

        # Listener metrics
        listeners: dict = {}
        if gingerbread_listener is not None:
            listeners["gingerbread"] = gingerbread_listener.get_metrics()
        if power_listener is not None:
            listeners["power"] = power_listener.get_metrics()
        payload["listeners"] = listeners

        # QoS diagnostics (from the QoSHandler inside the Gingerbread listener)
        if gingerbread_listener is not None:
            payload["qos"] = gingerbread_listener.qos_handler.get_diagnostics()
        else:
            payload["qos"] = {}

        # Telemetry row counts
        payload["telemetry"] = telemetry_svc.get_stats()

        return jsonify(payload)

    # ──────────────────────────────────────────────────────────────────────────
    # GET /api/health
    # ──────────────────────────────────────────────────────────────────────────

    @bp.route("/health", methods=["GET"])
    def health_check():
        """Simple liveness probe for the React frontend / load balancer to poll."""
        return jsonify({"status": "ok", "time": time.strftime("%Y-%m-%dT%H:%M:%S")})

    return bp


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _parse_limit(value, default: int = 50, max_val: int = 1000) -> int:
    try:
        n = int(value)
        return max(1, min(n, max_val))
    except (TypeError, ValueError):
        return default
