"""
backend/app/controllers/session_controller.py
─────────────────────────────────────────────────────────────────────────────
Web Controller — Session Management REST API.

Dedicated Blueprint for device session routes, separated from telemetry
for single-responsibility and easier future extension (e.g., add DELETE
to force-close a session, or POST to inject a test session).

All data reads are delegated to SessionService — this layer is a thin
HTTP adapter only.

Endpoints
─────────
  GET /api/sessions
      All sessions (active + asleep) as a JSON array.

  GET /api/sessions/stats
      Summary counts: total, active, asleep, timed_out.

  GET /api/sessions/<client_id>
      Single session by client_id, or 404.

Framework: Flask (swap for FastAPI by replacing @bp.route with @router.get).
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from flask import Blueprint, jsonify

if TYPE_CHECKING:
    from app.services.session_service import SessionService

logger = logging.getLogger(__name__)


def create_blueprint(session_svc: "SessionService") -> Blueprint:
    """
    Factory function that creates and returns the session API Blueprint.

    Parameters
    ----------
    session_svc : SessionService instance (required).
    """
    bp = Blueprint("sessions", __name__)

    # ──────────────────────────────────────────────────────────────────────────
    # GET /api/sessions
    # ──────────────────────────────────────────────────────────────────────────

    @bp.route("/sessions", methods=["GET"])
    def get_sessions():
        """
        Return the current in-memory device session table.

        Response shape:
          {
            "count": 2,
            "sessions": [
              {
                "client_id":    "nodeB_001",
                "addr_ip":      "192.168.1.42",
                "addr_port":    5000,
                "status":       "ACTIVE",
                "connected_at": 1716000000.0,
                "last_seen":    1716000060.0,
                "packet_count": 12
              },
              ...
            ]
          }
        """
        sessions = session_svc.get_all()
        return jsonify({"count": len(sessions), "sessions": sessions})

    # ──────────────────────────────────────────────────────────────────────────
    # GET /api/sessions/stats
    # ──────────────────────────────────────────────────────────────────────────

    @bp.route("/sessions/stats", methods=["GET"])
    def get_session_stats():
        """
        Return a summary of session lifecycle counts.

        Response shape:
          {
            "total":     3,
            "active":    1,
            "asleep":    2,
            "timed_out": 0,
            "server_time": "2026-05-29T17:00:00"
          }
        """
        stats = session_svc.get_stats()
        stats["server_time"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        return jsonify(stats)

    # ──────────────────────────────────────────────────────────────────────────
    # GET /api/sessions/<client_id>
    # ──────────────────────────────────────────────────────────────────────────

    @bp.route("/sessions/<string:client_id>", methods=["GET"])
    def get_session(client_id: str):
        """
        Return a single session by client_id, or 404 if not found.

        Response shape (200):
          { "client_id": "nodeB_001", "status": "ACTIVE", ... }

        Response shape (404):
          { "error": "Session 'nodeB_001' not found" }
        """
        sess = session_svc.get_by_client_id(client_id)
        if sess is None:
            return jsonify({"error": f"Session '{client_id}' not found"}), 404
        return jsonify(sess)

    return bp
