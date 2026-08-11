"""
backend/app/controllers/dashboard_controller.py
─────────────────────────────────────────────────────────────────────────────
Web Controller — 대시보드 전용 REST API (텔레메트리 조회 + CSV 로그).

대시보드 프론트엔드가 필요로 하는 데이터 조회 엔드포인트를 제공합니다.
모든 데이터 읽기는 TelemetryService에 위임 — CSV 파일이나 소켓을
직접 접근하지 않습니다.

엔드포인트
──────────
  GET /api/v1/telemetry/latest
      단말 최근 상태 1건 조회 (환경 데이터 + 추정 전력량 병합).

  GET /api/v1/logs/telemetry?limit=100
      logs/telemetry.csv 데이터를 JSON 배열로 반환.

  GET /api/v1/logs/power?limit=100
      logs/power.csv 데이터를 JSON 배열로 반환.

  GET /api/v1/logs/export?type=telemetry|power
      기존 CSV 파일을 .csv 형태로 직접 다운로드하는 FileResponse.

Framework: Flask Blueprint (기존 팩토리 패턴 준수).
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request, send_file

from config import LOG_DIR, TELEMETRY_CSV, POWER_CSV

if TYPE_CHECKING:
    from app.services.telemetry_service import TelemetryService

logger = logging.getLogger(__name__)

# CSV 타입 → 파일 경로 매핑
_CSV_TYPE_MAP = {
    "telemetry": TELEMETRY_CSV,
    "power":     POWER_CSV,
}


def create_blueprint(telemetry_svc: "TelemetryService") -> Blueprint:
    """
    대시보드 API 블루프린트 팩토리 함수.

    매개변수
    ────────
    telemetry_svc : TelemetryService 인스턴스 (의존성 주입).

    반환값
    ──────
    Flask Blueprint 객체.
    """
    bp = Blueprint("dashboard", __name__)

    # ──────────────────────────────────────────────────────────────────────────
    # GET /api/v1/telemetry/latest — 단말 최근 상태 1건 (환경 + 전력 병합)
    # ──────────────────────────────────────────────────────────────────────────

    @bp.route("/telemetry/latest", methods=["GET"])
    def get_latest_telemetry():
        """
        단말의 최근 상태 1건을 반환합니다.

        환경 센서 데이터(TelemetryService.get_latest_env())와
        추정 전력 데이터(TelemetryService.get_latest_power())를
        단일 JSON으로 병합하여 응답합니다.

        인메모리 스냅샷만 읽으므로 디스크 I/O가 발생하지 않습니다.

        응답 예시:
          {
            "environment": {
              "timestamp": "2026-08-11T16:00:00",
              "client_id": "nodeB_001",
              "temp":      25.3,
              "hum":       61.0,
              "gas":       98000.0,
              "gas_valid": true,
              ...
            },
            "power": {
              "nodeB_001": {
                "estimated_energy_mwh": 0.00012345,
                "rtt_ms": 12.3,
                ...
              }
            },
            "server_time": "2026-08-11T16:00:00"
          }
        """
        try:
            env_snapshot   = telemetry_svc.get_latest_env()
            power_snapshot = telemetry_svc.get_latest_power()

            return jsonify({
                "environment": env_snapshot,
                "power":       power_snapshot,
                "server_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }), 200

        except Exception as exc:
            logger.error(
                "[대시보드API] GET /api/v1/telemetry/latest 처리 중 오류: %s",
                exc, exc_info=True,
            )
            return jsonify({
                "status":  "error",
                "message": f"최근 상태 조회 중 서버 오류가 발생했습니다: {exc}",
            }), 500

    # ──────────────────────────────────────────────────────────────────────────
    # GET /api/v1/logs/telemetry?limit=100 — 텔레메트리 CSV 데이터 조회
    # ──────────────────────────────────────────────────────────────────────────

    @bp.route("/logs/telemetry", methods=["GET"])
    def get_telemetry_logs():
        """
        logs/telemetry.csv 데이터를 JSON 배열로 반환합니다.

        Query Parameters:
          limit (int, default 100) : 최대 반환 행 수 (1~1000).

        응답 예시:
          {
            "count": 42,
            "data":  [ { "timestamp": "...", "temp": 25.3, ... }, ... ]
          }
        """
        limit = _parse_limit(request.args.get("limit", 100))

        try:
            rows = telemetry_svc.get_recent_env(limit=limit)
            return jsonify({"count": len(rows), "data": rows}), 200

        except Exception as exc:
            logger.error(
                "[대시보드API] GET /api/v1/logs/telemetry 처리 중 오류: %s",
                exc, exc_info=True,
            )
            return jsonify({
                "status":  "error",
                "message": f"텔레메트리 로그 조회 중 서버 오류가 발생했습니다: {exc}",
            }), 500

    # ──────────────────────────────────────────────────────────────────────────
    # GET /api/v1/logs/power?limit=100 — 전력 CSV 데이터 조회
    # ──────────────────────────────────────────────────────────────────────────

    @bp.route("/logs/power", methods=["GET"])
    def get_power_logs():
        """
        logs/power.csv 데이터를 JSON 배열로 반환합니다.

        Query Parameters:
          limit (int, default 100) : 최대 반환 행 수 (1~1000).

        응답 예시:
          {
            "count": 50,
            "data":  [ { "timestamp": "...", "estimated_energy_mwh": 0.001, ... }, ... ]
          }
        """
        limit = _parse_limit(request.args.get("limit", 100))

        try:
            rows = telemetry_svc.get_recent_power(limit=limit)
            return jsonify({"count": len(rows), "data": rows}), 200

        except Exception as exc:
            logger.error(
                "[대시보드API] GET /api/v1/logs/power 처리 중 오류: %s",
                exc, exc_info=True,
            )
            return jsonify({
                "status":  "error",
                "message": f"전력 로그 조회 중 서버 오류가 발생했습니다: {exc}",
            }), 500

    # ──────────────────────────────────────────────────────────────────────────
    # GET /api/v1/logs/export?type=telemetry|power — CSV 파일 직접 다운로드
    # ──────────────────────────────────────────────────────────────────────────

    @bp.route("/logs/export", methods=["GET"])
    def export_csv():
        """
        기존 CSV 파일을 .csv 형태로 직접 다운로드합니다.

        Query Parameters:
          type (str, 필수) : "telemetry" 또는 "power".

        성공 응답: CSV 파일 (Content-Disposition: attachment)
        오류 응답:
          400 — type 파라미터 누락 또는 유효하지 않은 값
          404 — CSV 파일이 존재하지 않음
          500 — 서버 내부 오류
        """
        csv_type = request.args.get("type", "").strip().lower()

        # ── type 파라미터 검증 ────────────────────────────────────────────
        if csv_type not in _CSV_TYPE_MAP:
            valid_types = ", ".join(sorted(_CSV_TYPE_MAP.keys()))
            logger.warning(
                "[대시보드API] GET /api/v1/logs/export — 유효하지 않은 type: '%s'",
                csv_type,
            )
            return jsonify({
                "status":  "error",
                "message": f"'type' 파라미터는 다음 중 하나여야 합니다: {valid_types}",
            }), 400

        csv_path = _CSV_TYPE_MAP[csv_type]

        # ── 파일 존재 여부 확인 ───────────────────────────────────────────
        if not os.path.exists(csv_path):
            logger.warning(
                "[대시보드API] GET /api/v1/logs/export — 파일 미존재: %s", csv_path
            )
            return jsonify({
                "status":  "error",
                "message": f"'{csv_type}.csv' 파일이 존재하지 않습니다.",
            }), 404

        # ── 파일 전송 ────────────────────────────────────────────────────
        try:
            download_name = f"{csv_type}.csv"
            logger.info(
                "[대시보드API] CSV 다운로드 요청: type=%s, path=%s, 요청 출처=%s",
                csv_type, csv_path, request.remote_addr,
            )
            return send_file(
                csv_path,
                mimetype="text/csv",
                as_attachment=True,
                download_name=download_name,
            )

        except Exception as exc:
            logger.error(
                "[대시보드API] GET /api/v1/logs/export 처리 중 오류: %s",
                exc, exc_info=True,
            )
            return jsonify({
                "status":  "error",
                "message": f"CSV 파일 다운로드 중 서버 오류가 발생했습니다: {exc}",
            }), 500

    return bp


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _parse_limit(value, default: int = 100, max_val: int = 1000) -> int:
    """limit 쿼리 파라미터를 안전하게 정수로 변환합니다."""
    try:
        n = int(value)
        return max(1, min(n, max_val))
    except (TypeError, ValueError):
        return default
