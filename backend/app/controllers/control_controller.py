"""
backend/app/controllers/control_controller.py
─────────────────────────────────────────────────────────────────────────────
Web Controller — 다운링크 제어 REST API.

대시보드에서 ESP32-S3 디바이스로 제어 명령을 전송하기 위한
HTTP 어댑터 계층입니다. 모든 비즈니스 로직은 ControlService에 위임합니다.

엔드포인트
──────────
  POST /api/v1/control
      ESP32-S3 디바이스로 다운링크 제어 패킷을 UDP 전송합니다.

      Request Body (application/json):
        {
          "device_ip":      "192.168.x.x",
          "device_port":    8888,
          "qos_level":      1,
          "sleep_interval": 1000
        }

      성공 응답 (200):
        {
          "status":      "ok",
          "message":     "다운링크 제어 패킷이 전송되었습니다.",
          "msg_id":      42,
          "packet_size": 136,
          "target":      "192.168.1.42:8888",
          "timestamp":   "2026-08-11T16:00:00"
        }

      오류 응답:
        400 — 입력값 유효성 검증 실패
        502 — UDP 전송 실패 (네트워크 오류)
        500 — 서버 내부 오류

Framework: Flask Blueprint (기존 패턴 준수).
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request

if TYPE_CHECKING:
    from app.services.control_service import ControlService

logger = logging.getLogger(__name__)


def create_blueprint(control_svc: "ControlService") -> Blueprint:
    """
    다운링크 제어 API 블루프린트 팩토리 함수.

    매개변수
    ────────
    control_svc : ControlService 인스턴스 (의존성 주입).

    반환값
    ──────
    Flask Blueprint 객체.
    """
    bp = Blueprint("control", __name__)

    # ──────────────────────────────────────────────────────────────────────────
    # POST /api/v1/control — 다운링크 제어 패킷 전송
    # ──────────────────────────────────────────────────────────────────────────

    @bp.route("/control", methods=["POST"])
    def send_control():
        """
        ESP32-S3 디바이스로 다운링크 제어 패킷을 전송합니다.

        요청 본문 (Content-Type: application/json):
          {
            "device_ip":      "192.168.x.x",   (필수, 유효한 IPv4)
            "device_port":    8888,             (필수, 1~65535)
            "qos_level":      1,                (필수, 0/1/2)
            "sleep_interval": 1000              (필수, 양의 정수 ms)
          }
        """
        # ── 요청 본문 파싱 ────────────────────────────────────────────────
        body = request.get_json(force=True, silent=True)

        if body is None:
            logger.warning("[제어API] POST /api/v1/control — 유효하지 않은 JSON 요청 본문")
            return jsonify({
                "status":  "error",
                "message": "요청 본문이 비어있거나 유효한 JSON 형식이 아닙니다.",
            }), 400

        # ── 필수 필드 확인 ────────────────────────────────────────────────
        required_fields = ("device_ip", "device_port", "qos_level", "sleep_interval")
        missing = [f for f in required_fields if f not in body]
        if missing:
            logger.warning(
                "[제어API] POST /api/v1/control — 필수 필드 누락: %s", missing
            )
            return jsonify({
                "status":  "error",
                "message": f"필수 필드가 누락되었습니다: {', '.join(missing)}",
            }), 400

        # ── 타입 변환 (프론트엔드 호환) ───────────────────────────────────
        try:
            device_ip      = str(body["device_ip"]).strip()
            device_port    = int(body["device_port"])
            qos_level      = int(body["qos_level"])
            sleep_interval = int(body["sleep_interval"])
        except (TypeError, ValueError) as exc:
            logger.warning(
                "[제어API] POST /api/v1/control — 타입 변환 실패: %s", exc
            )
            return jsonify({
                "status":  "error",
                "message": f"필드 값의 타입이 올바르지 않습니다: {exc}",
            }), 400

        logger.info(
            "[제어API] POST /api/v1/control 수신 | ip=%s port=%d qos=%d sleep=%d ms | 요청 출처=%s",
            device_ip, device_port, qos_level, sleep_interval,
            request.remote_addr,
        )

        # ── ControlService 호출 ──────────────────────────────────────────
        try:
            result = control_svc.send_downlink(
                device_ip=device_ip,
                device_port=device_port,
                qos_level=qos_level,
                sleep_interval=sleep_interval,
            )
            return jsonify({
                **result,
                "message":   "다운링크 제어 패킷이 전송되었습니다.",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }), 200

        except ValueError as exc:
            # 입력값 유효성 검증 실패 — 클라이언트 오류
            logger.warning("[제어API] 입력값 유효성 검증 실패: %s", exc)
            return jsonify({
                "status":  "error",
                "message": str(exc),
            }), 400

        except OSError as exc:
            # UDP 전송 실패 — 네트워크/디바이스 오류
            logger.error("[제어API] UDP 전송 실패: %s", exc)
            return jsonify({
                "status":  "error",
                "message": f"디바이스로의 UDP 전송에 실패했습니다: {exc}",
            }), 502

        except Exception as exc:
            # 예상치 못한 서버 내부 오류
            logger.error(
                "[제어API] POST /api/v1/control 처리 중 예외 발생: %s",
                exc, exc_info=True,
            )
            return jsonify({
                "status":  "error",
                "message": f"서버 내부 오류가 발생했습니다: {exc}",
            }), 500

    return bp
