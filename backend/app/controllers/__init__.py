"""
backend/app/controllers/__init__.py
─────────────────────────────────────────────────────────────────────────────
Public API for the controllers package.

Import from here rather than from individual modules:
  from app.controllers import (
      create_telemetry_blueprint,
      create_session_blueprint,
      create_control_blueprint,
      create_dashboard_blueprint,
  )
─────────────────────────────────────────────────────────────────────────────
"""

from app.controllers.telemetry_controller  import create_blueprint as create_telemetry_blueprint
from app.controllers.session_controller    import create_blueprint as create_session_blueprint
from app.controllers.control_controller    import create_blueprint as create_control_blueprint
from app.controllers.dashboard_controller  import create_blueprint as create_dashboard_blueprint

__all__ = [
    "create_telemetry_blueprint",
    "create_session_blueprint",
    "create_control_blueprint",
    "create_dashboard_blueprint",
]
