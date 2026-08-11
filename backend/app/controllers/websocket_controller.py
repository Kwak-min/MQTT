"""
backend/app/controllers/websocket_controller.py
─────────────────────────────────────────────────────────────────────────────
WebSocket Controller — 실시간 텔레메트리 스트리밍.

flask-sock 기반 WebSocket 엔드포인트를 제공합니다.
기존 Flask SSE(/api/telemetry/stream)를 보완하는 양방향 실시간 채널입니다.

엔드포인트
──────────
  WS /ws/telemetry
      실시간 환경 텔레메트리 + 추정 전력량(mWh) 데이터를 JSON으로 스트리밍합니다.
      TelemetryService.subscribe_env()를 통해 데이터를 수신하며,
      연결 해제 시 자동으로 unsubscribe_env()를 호출합니다.

      전송되는 JSON 메시지 예시:
        {
          "type": "telemetry",
          "data": {
            "timestamp": "2026-08-11T16:00:00",
            "client_id": "nodeB_001",
            "temp":      25.3,
            "hum":       61.0,
            "gas":       98000.0,
            "gas_valid": true,
            "power":     null,
            "estimated_energy_mwh": 0.00012345
          }
        }

      하트비트 메시지 (30초 간격):
        { "type": "heartbeat", "timestamp": "..." }

의존성:
  flask-sock>=0.7.0 (requirements.txt에 추가됨)

사용법 (main.py):
  from app.controllers.websocket_controller import init_websocket
  init_websocket(flask_app, telemetry_svc)
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask import Flask
    from app.services.telemetry_service import TelemetryService

logger = logging.getLogger(__name__)

# WebSocket 큐에서 데이터를 기다리는 최대 시간 (초)
# 이 시간이 지나면 하트비트 메시지를 전송합니다.
_HEARTBEAT_INTERVAL: float = 30.0


def init_websocket(
    app: "Flask",
    telemetry_svc: "TelemetryService",
) -> None:
    """
    Flask 앱에 WebSocket 엔드포인트를 등록합니다.

    flask-sock가 설치되어 있지 않은 경우 경고를 출력하고 정상 반환합니다.
    — WebSocket 없이도 나머지 REST API는 계속 작동합니다.

    매개변수
    ────────
    app           : Flask 앱 인스턴스.
    telemetry_svc : TelemetryService 인스턴스 (실시간 데이터 소스).
    """
    try:
        from flask_sock import Sock
    except ImportError as exc:
        logger.warning(
            "[WebSocket] flask-sock 패키지가 없어 WebSocket 엔드포인트가 비활성화됩니다. "
            "설치:  pip install flask-sock\n  Detail: %s", exc,
        )
        return

    sock = Sock(app)

    @sock.route("/ws/telemetry")
    def ws_telemetry(ws):
        """
        실시간 텔레메트리 WebSocket 핸들러.

        연결이 유지되는 동안 TelemetryService의 SSE 구독 큐에서
        데이터를 수신하여 WebSocket으로 전달합니다.
        """
        client_addr = "unknown"
        q = telemetry_svc.subscribe_env()
        logger.info("[WebSocket] 클라이언트 연결됨: %s", client_addr)

        try:
            # 연결 확인 메시지 전송
            ws.send(json.dumps({
                "type":      "connected",
                "message":   "텔레메트리 WebSocket 스트림에 연결되었습니다.",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }))

            while True:
                try:
                    # 큐에서 데이터 대기 (타임아웃 시 하트비트 전송)
                    row = q.get(timeout=_HEARTBEAT_INTERVAL)

                    # 추정 전력량(mWh) 데이터 병합
                    power_snapshot = telemetry_svc.get_latest_power()
                    estimated_energy = _extract_estimated_energy(power_snapshot)

                    message = {
                        "type": "telemetry",
                        "data": {
                            **row,
                            "estimated_energy_mwh": estimated_energy,
                        },
                    }
                    ws.send(json.dumps(message, default=str))

                except Exception:
                    # 큐 타임아웃 — 하트비트 전송
                    try:
                        ws.send(json.dumps({
                            "type":      "heartbeat",
                            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        }))
                    except Exception:
                        # WebSocket 연결 끊김
                        break

        except Exception as exc:
            logger.debug("[WebSocket] 연결 종료 (클라이언트: %s): %s", client_addr, exc)

        finally:
            telemetry_svc.unsubscribe_env(q)
            logger.info("[WebSocket] 클라이언트 연결 해제됨: %s", client_addr)

    logger.info("[WebSocket] WS /ws/telemetry 엔드포인트 등록 완료.")


def _extract_estimated_energy(power_snapshot: dict) -> float | None:
    """
    전력 스냅샷에서 최신 추정 에너지(mWh) 값을 추출합니다.

    power_snapshot은 노드별 딕셔너리 또는 단일 노드 딕셔너리일 수 있습니다.
    첫 번째로 발견되는 estimated_energy_mwh 값을 반환합니다.

    반환값
    ------
    float | None — 추정 에너지 (mWh), 또는 데이터 없으면 None.
    """
    if not isinstance(power_snapshot, dict):
        return None

    # 단일 노드 스냅샷인 경우
    if "estimated_energy_mwh" in power_snapshot:
        return power_snapshot["estimated_energy_mwh"]

    # 노드별 딕셔너리인 경우 — 첫 번째 유효한 값 반환
    for _node_key, node_data in power_snapshot.items():
        if isinstance(node_data, dict) and "estimated_energy_mwh" in node_data:
            return node_data["estimated_energy_mwh"]

    return None
