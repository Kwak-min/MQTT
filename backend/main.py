"""
backend/main.py
─────────────────────────────────────────────────────────────────────────────
애플리케이션 진입점(Entry point)입니다.

백엔드의 모든 계층을 연결합니다:
  1. 서비스(SessionService, TelemetryService) 인스턴스화
  2. 소켓과 서비스 계층을 연결하는 콜백 함수 설정
  3. UDP 리스너 스레드 인스턴스화 및 시작
  4. 데몬 스레드에서 Flask REST API 서버 시작
  5. KeyboardInterrupt가 발생할 때까지 메인 스레드 대기

backend/ 디렉토리에서 실행 방법:
  python main.py

또는 프로젝트 루트에서 실행 방법:
  python -m backend
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
import os
import sys
import threading

# ── 하위 패키지가 올바르게 확인되도록 sys.path에 `backend/`를 추가합니다 ──
# 이를 통해 설치하지 않고도 backend/ 내부에서 `python main.py`를 실행할 수 있습니다.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# ─────────────────────────────────────────────────────────────────────────────
# 다른 모듈을 임포트하기 전에 로깅 구성
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
# 임포트 (sys.path 수정 후)
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
    # 1. 서비스
    # ──────────────────────────────────────────────────────────────────────────
    session_svc   = SessionService()
    telemetry_svc = TelemetryService()
    logger.info("[Main] Services initialised.")

    # ──────────────────────────────────────────────────────────────────────────
    # 2. 콜백 클로저 (소켓 계층 → 서비스 계층 브릿지)
    # ──────────────────────────────────────────────────────────────────────────

    def on_connect(packet: ConnectPacket) -> None:
        session_svc.on_connect(packet)

    def on_disconnect(packet: DisconnectPacket) -> None:
        session_svc.on_disconnect(packet)

    def on_env_deliver(packet: PublishPacket) -> None:
        """
        PUBLISH가 확인되면(모든 QoS 수준) QoSHandler에 의해 호출됩니다.
        로깅하기 전에 세션 테이블에서 client_id를 확인합니다.
        """
        # 발신자 주소로 세션 테이블에서 client_id 확인
        client_id = None
        for sess in session_svc.get_all():
            if sess["addr_ip"] == packet.addr[0] and sess["addr_port"] == packet.addr[1]:
                client_id = sess["client_id"]
                break

        session_svc.increment_packet_count(packet.addr)
        telemetry_svc.record_env_telemetry(packet, client_id=client_id)

    def on_power(packet: PowerPacket) -> None:
        """모든 전력 MCU 스트리밍 패킷에 대해 호출됩니다."""
        telemetry_svc.record_power_telemetry(packet)

    # ──────────────────────────────────────────────────────────────────────────
    # 3. UDP 리스너
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
    # 4. Flask REST API (데몬 스레드 — 선택 사항, 주석 처리해도 안전함)
    # ──────────────────────────────────────────────────────────────────────────
    _start_flask_api(
        session_svc=session_svc,
        telemetry_svc=telemetry_svc,
        gingerbread_listener=gingerbread_listener,
        power_listener=power_listener,
    )

    # ──────────────────────────────────────────────────────────────────────────
    # 5. 메인 스레드 유지
    # ──────────────────────────────────────────────────────────────────────────
    logger.info("[Main] Backend running. Press Ctrl+C to stop.")
    shutdown_event = threading.Event()
    try:
        shutdown_event.wait()   # KeyboardInterrupt가 발생할 때까지 대기
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
    백그라운드 데몬 스레드에서 Flask REST API를 시작합니다.
    React/Vue 개발 서버에서 접근할 수 있도록 0.0.0.0:8080에 바인딩됩니다.

    두 블루프린트를 등록합니다:
      /api/telemetry/...  — 환경 + 전력 데이터 + SSE 스트림
      /api/sessions/...   — 디바이스 세션 테이블
      /api/diagnostics    — QoS 카운터 + 리스너 상태
      /api/health         — 활성 상태 점검(liveness probe)

    Flask가 설치되어 있지 않은 경우, 이 함수는 경고를 로깅하고 정상적으로 반환됩니다.
    — REST API가 없어도 UDP 게이트웨이는 계속 작동합니다.
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
    # 개발 중에는 React/Vue 개발 서버(localhost:3000)를 허용합니다.
    CORS(flask_app, resources={r"/api/*": {"origins": "*"}})

    # 원격 분석(telemetry) 블루프린트 등록 (/api/telemetry/*, /api/diagnostics, /api/health 포함)
    telemetry_bp = create_telemetry_bp(
        telemetry_svc=telemetry_svc,
        gingerbread_listener=gingerbread_listener,
        power_listener=power_listener,
    )
    flask_app.register_blueprint(telemetry_bp, url_prefix="/api")

    # 세션 블루프린트 등록 (/api/sessions/*)
    session_bp = create_session_bp(session_svc=session_svc)
    flask_app.register_blueprint(session_bp, url_prefix="/api")

    api_thread = threading.Thread(
        target=lambda: flask_app.run(
            host="0.0.0.0",
            port=8080,
            debug=False,        # 메인 스레드가 아닌 경우 반드시 False여야 함
            use_reloader=False,
            threaded=True,      # SSE에 필요함 — 각 SSE 클라이언트는 자체 스레드를 가짐
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
    logger.info("[Main]       GET  /api/telemetry/latest/env    (in-memory, no disk)")
    logger.info("[Main]       GET  /api/telemetry/latest/power  (in-memory, no disk)")
    logger.info("[Main]       GET  /api/telemetry/stream  (SSE)")
    logger.info("[Main]       GET  /api/sessions")
    logger.info("[Main]       GET  /api/sessions/stats")
    logger.info("[Main]       GET  /api/sessions/<client_id>")


if __name__ == "__main__":
    main()
