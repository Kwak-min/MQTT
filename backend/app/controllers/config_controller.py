"""
backend/app/controllers/config_controller.py
─────────────────────────────────────────────────────────────────────────────
웹 대시보드 ↔ 게이트웨이 서버 설정 동기화 REST API 컨트롤러.

이 블루프린트는 ConfigService에 대한 얇은 HTTP 어댑터 역할만 담당합니다.
비즈니스 로직, 파일 I/O, MQTT 발행은 모두 ConfigService가 처리합니다.

엔드포인트
──────────
  GET  /api/config
      현재 활성 설정 전체를 반환합니다 (메모리 캐시, 디스크 I/O 없음).

  POST /api/config
      웹 대시보드에서 변경된 파라미터를 수신하고,
      config.json에 저장 후 MQTT로 ESP32에 즉시 푸시합니다.
      지원 파라미터 (플랫 또는 섹션 중첩 JSON 형식 모두 허용):
        - RSSI_THRESHOLD (int/float, -120~0 dBm)
        - PACKET_LOSS_LIMIT (int/float, 0~100 %)
        - GAS_THRESHOLD_KOHM (int/float, 양수)
        - TEMP_THRESHOLD_CELSIUS (int/float, -40~125 °C)
        - POWER_MODE (str, "EXTERNAL_5V" | "BATTERY")
        - CURRENT_BATTERY_LEVEL (int, 0~100)

  POST /api/config/reload
      디스크의 config.json을 다시 읽어 메모리를 갱신합니다.
      외부에서 파일을 직접 수정한 경우 유용합니다.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request

if TYPE_CHECKING:
    # 순환 임포트 방지를 위해 타입 힌팅 전용 임포트
    from app.services.config_service import ConfigService

logger = logging.getLogger(__name__)


def create_blueprint(config_svc: "ConfigService") -> Blueprint:
    """
    설정 API 블루프린트 팩토리 함수.

    매개변수
    ────────
    config_svc : ConfigService 인스턴스 (의존성 주입).
                 main.py에서 생성된 단일 인스턴스를 공유합니다.

    반환값
    ──────
    Flask Blueprint 객체.
    """
    bp = Blueprint("config", __name__)

    # ──────────────────────────────────────────────────────────────────────────
    # GET /api/config — 현재 설정 전체 조회
    # ──────────────────────────────────────────────────────────────────────────

    @bp.route("/config", methods=["GET"])
    def get_config():
        """
        현재 메모리에 캐싱된 설정 전체를 반환합니다.
        디스크 I/O 없이 즉시 응답합니다.

        응답 예시:
          {
            "status": "ok",
            "config": {
              "NETWORK": { "RSSI_THRESHOLD": -80, "PACKET_LOSS_LIMIT": 5 },
              "ENVIRONMENT": { "GAS_THRESHOLD_KOHM": 20, "TEMP_THRESHOLD_CELSIUS": 45 },
              "POWER_MANAGEMENT": { "POWER_MODE": "EXTERNAL_5V", "CURRENT_BATTERY_LEVEL": 100 }
            },
            "timestamp": "2026-06-02T21:00:00"
          }
        """
        try:
            current_config = config_svc.get_config()
            return jsonify({
                "status":    "ok",
                "config":    current_config,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }), 200

        except Exception as exc:
            # 예상치 못한 서버 내부 오류
            logger.error("[설정API] GET /api/config 처리 중 오류: %s", exc)
            return jsonify({
                "status":  "error",
                "message": f"설정 조회 중 서버 오류가 발생했습니다: {exc}",
            }), 500

    # ──────────────────────────────────────────────────────────────────────────
    # POST /api/config — 설정 업데이트 (웹 대시보드 → 게이트웨이 → ESP32)
    # ──────────────────────────────────────────────────────────────────────────

    @bp.route("/config", methods=["POST"])
    def update_config():
        """
        웹 대시보드에서 전달된 설정 파라미터를 검증하고,
        config.json에 저장한 후 MQTT 브로커를 통해 ESP32에 즉시 푸시합니다.

        요청 본문 (Content-Type: application/json):
          플랫(flat) 형식:
            {
              "RSSI_THRESHOLD": -75,
              "POWER_MODE": "BATTERY",
              "CURRENT_BATTERY_LEVEL": 85
            }

          또는 섹션 중첩 형식:
            {
              "NETWORK": { "RSSI_THRESHOLD": -75 },
              "POWER_MANAGEMENT": { "POWER_MODE": "BATTERY" }
            }

        성공 응답 (200):
          {
            "status": "ok",
            "message": "설정이 저장되고 ESP32에 MQTT로 전송되었습니다.",
            "config": { ... 갱신된 전체 설정 ... },
            "timestamp": "2026-06-02T21:00:00"
          }

        오류 응답 (400 — 잘못된 입력값):
          { "status": "error", "message": "유효성 검증 실패: ..." }

        오류 응답 (500 — 서버 내부 오류):
          { "status": "error", "message": "서버 오류: ..." }
        """
        # ── 요청 본문 파싱 ────────────────────────────────────────────────────
        # Content-Type이 application/json이 아닌 경우에도 force=True로 파싱 시도
        updates = request.get_json(force=True, silent=True)

        if updates is None:
            # 요청 본문이 없거나 JSON 파싱 실패
            logger.warning("[설정API] POST /api/config — 유효하지 않은 JSON 요청 본문")
            return jsonify({
                "status":  "error",
                "message": "요청 본문이 비어있거나 유효한 JSON 형식이 아닙니다.",
            }), 400

        if not isinstance(updates, dict) or len(updates) == 0:
            # 빈 딕셔너리 또는 딕셔너리가 아닌 경우
            logger.warning("[설정API] POST /api/config — 빈 요청 또는 잘못된 형식")
            return jsonify({
                "status":  "error",
                "message": "변경할 파라미터가 없거나 올바른 딕셔너리 형식이 아닙니다.",
            }), 400

        logger.info(
            "[설정API] POST /api/config 수신 — 파라미터 수: %d, 요청 출처: %s",
            len(updates),
            request.remote_addr,
        )

        try:
            # ConfigService가 검증 → 저장 → MQTT 발행을 순서대로 처리합니다.
            updated_config = config_svc.update_config(updates)
            return jsonify({
                "status":    "ok",
                "message":   "설정이 저장되고 ESP32에 MQTT(gingerbread/config)로 전송되었습니다.",
                "config":    updated_config,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }), 200

        except ValueError as exc:
            # 입력값 유효성 검증 실패 — 클라이언트 오류
            logger.warning("[설정API] 유효성 검증 실패: %s", exc)
            return jsonify({
                "status":  "error",
                "message": str(exc),
            }), 400

        except OSError as exc:
            # config.json 파일 쓰기 실패 — 서버 파일시스템 오류
            logger.error("[설정API] config.json 저장 실패: %s", exc)
            return jsonify({
                "status":  "error",
                "message": f"설정 파일 저장에 실패했습니다: {exc}",
            }), 500

        except Exception as exc:
            # 예상치 못한 서버 내부 오류 (MQTT 연결 오류 포함)
            logger.error("[설정API] POST /api/config 처리 중 예외 발생: %s", exc, exc_info=True)
            return jsonify({
                "status":  "error",
                "message": f"설정 처리 중 서버 오류가 발생했습니다: {exc}",
            }), 500

    # ──────────────────────────────────────────────────────────────────────────
    # POST /api/config/reload — 디스크에서 설정 강제 재로드
    # ──────────────────────────────────────────────────────────────────────────

    @bp.route("/config/reload", methods=["POST"])
    def reload_config():
        """
        디스크의 config.json을 다시 읽어 메모리 캐시를 갱신합니다.

        외부 도구(텍스트 에디터, 배포 스크립트 등)가 config.json을 직접 수정한
        경우에 서버 재시작 없이 즉시 반영하려 할 때 사용합니다.

        성공 응답 (200):
          {
            "status": "ok",
            "message": "config.json이 성공적으로 재로드되었습니다.",
            "config": { ... 재로드된 전체 설정 ... },
            "timestamp": "..."
          }
        """
        try:
            reloaded_config = config_svc.load_config()
            logger.info(
                "[설정API] POST /api/config/reload — 설정 재로드 완료 (요청 출처: %s)",
                request.remote_addr,
            )
            return jsonify({
                "status":    "ok",
                "message":   "config.json이 성공적으로 재로드되었습니다.",
                "config":    reloaded_config,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }), 200

        except Exception as exc:
            logger.error("[설정API] 설정 재로드 중 오류: %s", exc)
            return jsonify({
                "status":  "error",
                "message": f"설정 재로드 중 서버 오류가 발생했습니다: {exc}",
            }), 500

    return bp
