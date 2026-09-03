"""
backend/app/services/telemetry_service.py
─────────────────────────────────────────────────────────────────────────────
텔레메트리 서비스 — 데이터 수신과 저장/조회를 분리하는 핵심 서비스 레이어.

소켓/QoS 레이어로부터 확인된 패킷을 수신하고 다음을 수행합니다:
  1. 영속 저장소에 기록    (현재: CSV 파일)
  2. 인메모리 스냅샷 갱신  → REST API가 디스크 접근 없이 즉시 응답 가능
  3. SSE 구독자 큐에 실시간 팬아웃  (GET /api/telemetry/stream)
  4. REST 컨트롤러가 호출하는 조회 메서드 제공

인메모리 스냅샷 설계
──────────────────────
  self.telemetry_data  — 최신 환경 데이터 단일 전역 스냅샷 (ESP32-S3 Node B)
  self.power_data      — 노드("A" / "B")별 최신 전력 데이터

  두 딕셔너리 모두 안전한 기본값(0.0)으로 초기화되므로, 첫 패킷이 도착하기
  전에도 REST API는 유효한 JSON을 반환합니다. gas 필드 포함 전 필드가 항상
  존재하므로 컨트롤러에서 KeyError가 절대 발생하지 않습니다.

저장소 교체 가이드
──────────────────
  _write_env_row() 와 _write_power_row() 를 DB 삽입 호출로 교체하면 됩니다.
  퍼블릭 인터페이스는 그대로 유지되므로 다른 파일을 수정할 필요가 없습니다.

  예시 (InfluxDB):
    def _write_env_row(self, row: dict) -> None:
        point = Point("environment") \\
            .tag("client_id", row["client_id"]) \\
            .field("temp",    row["temp"]) ...
        influx_client.write(point)

처리하는 데이터 스트림
─────────────────────
  1. 환경 텔레메트리  (Node B PUBLISH 페이로드 — BME680 센서)
       필드  : temp (°C), hum (%), gas (Ω 저항값), power (mW)
       추가  : gas_valid (bool) — BME680 물리적 유효 범위 [1 kΩ, 10 MΩ]
               밖이거나 페이로드에 없으면 False

  2. 전력 텔레메트리  (ESP32-C3 전력 측정 MCU JSON 스트림)
       필드  : node, current_mA, voltage_V, power_mW
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import copy
import csv
import logging
import os
import queue
import threading
import time
from typing import Dict, List, Optional

from app.models.packet import PowerPacket, PublishPacket, EstimatedPowerPacket
from app.services.power_estimator import estimate_energy
from config import LOG_DIR, POWER_CSV, TELEMETRY_CSV

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# 기본값 스냅샷 템플릿 — 첫 패킷 도착 전 REST API가 반환하는 초기 상태
# ──────────────────────────────────────────────────────────────────────────────

# 환경 데이터 기본 스냅샷.
# 모든 센서 필드가 0.0으로 초기화되어 JSON 직렬화가 절대 실패하지 않습니다.
_DEFAULT_ENV_SNAPSHOT: dict = {
    "timestamp":    None,    # str | None — 마지막 갱신 시각 (ISO-8601)
    "client_id":    None,    # str | None — 세션 테이블에서 조회한 디바이스 ID
    "msg_id":       None,    # int | None — 패킷 메시지 ID
    "qos":          None,    # int | None — QoS 레벨 (0, 1, 2)
    "topic_id":     None,    # int | None — 토픽 식별자
    "addr_ip":      None,    # 송신자 IP
    "addr_port":    None,    # 송신자 포트
    # ── BME680 센서 측정값 ────────────────────────────────────────────────────
    "temp":         0.0,     # float — 온도 (°C)
    "hum":          0.0,     # float — 상대 습도 (%)
    "gas":          0.0,     # float — BME680 가스 저항값 (Ω)
    "gas_valid":    False,   # bool  — True: 유효 범위 내, False: 범위 이탈 또는 미수신
    "power":        0.0,     # float — 펌웨어 옵션 필드 (mW)
    # ── 메타 ──────────────────────────────────────────────────────────────────
    "raw_payload":  None,    # 디버깅용 원본 페이로드 문자열
    "packet_count": 0,       # 누적 PUBLISH 패킷 수신 카운터
}

# 노드별 전력 데이터 기본 스냅샷 ("A", "B" 공통 템플릿)
_DEFAULT_POWER_NODE: dict = {
    "timestamp":    None,
    "addr_ip":      None,
    "addr_port":    None,
    "current_mA":   0.0,    # 순시 전류 (mA)
    "voltage_V":    0.0,    # 공급 전압 (V)
    "power_mW":     0.0,    # 순시 전력 (mW)
    "sample_count": 0,      # 해당 노드 누적 전력 샘플 수
}


class TelemetryService:
    """
    ESP32 노드로부터 수신한 모든 텔레메트리 데이터의 영속화,
    인메모리 캐싱, 실시간 스트리밍을 담당하는 서비스 클래스.

    스레드 안전성:
      포트 5000(Gingerbread)과 6000(전력 MCU) 리스너 스레드가 동시에
      이 서비스를 호출할 수 있습니다. 각 데이터 스트림은 독립적인
      RLock으로 보호됩니다.

    인메모리 스냅샷 (self.telemetry_data / self.power_data)
    ─────────────────────────────────────────────────────
      항상 유효한 딕셔너리입니다. REST 컨트롤러는 get_latest_env() /
      get_latest_power() 를 언제든 호출해도 CSV 읽기 없이 즉시 완전한
      응답을 받습니다 — 서버 시작 직후에도 마찬가지입니다.

    SSE 팬아웃
    ──────────
      subscribe_env() / unsubscribe_env() 를 통해 HTTP 클라이언트가
      실시간 환경 데이터 스트림을 구독합니다. 새 데이터가 도착하면
      모든 구독자 큐에 행 딕셔너리가 즉시 전달됩니다.
    """

    # ── CSV 필드 정의 (단일 진실 공급원) ─────────────────────────────────────

    _ENV_FIELDS: List[str] = [
        "timestamp", "client_id", "msg_id", "qos", "topic_id",
        "addr_ip", "addr_port",
        # BME680 센서 측정값
        "temp",        # float | None  — 온도 (°C)
        "hum",         # float | None  — 상대 습도 (%)
        "gas",         # float | None  — 가스 저항값 (Ω) ← 이번 업데이트 핵심 필드
        "gas_valid",   # bool          — BME680 물리적 유효 범위 내 여부
        "power",       # float | None  — 펌웨어 옵션 전력 필드
        "raw_payload",
    ]

    # [2026-06 리팩토링] 전력 CSV 필드 스키마 교체
    # 하드웨어 INA219 측정값 → IEEE Access 2024 소프트웨어 추정값
    _POWER_FIELDS: List[str] = [
        "timestamp",
        "client_id",          # ESP32 클라이언트 ID
        "qos",                # QoS 레벨 (0, 1, 2)
        "rtt_ms",             # PUBLISH~ACK 왕복 시간 (ms)
        "retry_count",        # 재전송 횟수
        "sleep_mode_ratio",   # Sleep 비율 (0.0~1.0)
        "estimated_energy_mwh",  # IEEE Access 2024 추정 에너지 (mWh)
        "packet_count",       # 디바이스 누적 패킷 수
        "total_bytes",        # 디바이스 누적 전송 바이트 수
    ]

    # ── BME680 가스 저항 물리적 유효 범위 (kΩ 단위) ─────────────────────────
    # 1 kΩ 미만 : 히터가 아직 예열 중이거나 단락(쇼트) 상태
    # 10 MΩ 초과 : 센서 측정 범위 초과 / 매우 청정한 공기
    _GAS_MIN_OHM: float = 1.0       # 1 kΩ
    _GAS_MAX_OHM: float = 10_000.0  # 10,000 kΩ (10 MΩ)

    # ──────────────────────────────────────────────────────────────────────────

    def __init__(self) -> None:
        # 로그 디렉토리가 없으면 생성
        os.makedirs(LOG_DIR, exist_ok=True)

        # 스트림별 재진입 가능 락 (같은 스레드의 재진입 허용)
        self._env_lock   = threading.RLock()
        self._power_lock = threading.RLock()

        # ── 인메모리 스냅샷 초기화 ────────────────────────────────────────────
        # 템플릿을 deepcopy하여 인스턴스 간 상태가 공유되지 않도록 보장합니다.
        # REST 컨트롤러는 get_latest_env() / get_latest_power() 로 이를 읽습니다.
        self.telemetry_data: dict = copy.deepcopy(_DEFAULT_ENV_SNAPSHOT)

        # 노드별 전력 스냅샷: { "A": {...}, "B": {...} }
        self.power_data: Dict[str, dict] = {
            "A": copy.deepcopy(_DEFAULT_POWER_NODE),
            "B": copy.deepcopy(_DEFAULT_POWER_NODE),
        }

        # ── SSE 구독자 목록 ───────────────────────────────────────────────────
        self._sse_lock:        threading.Lock    = threading.Lock()
        self._sse_subscribers: List[queue.Queue] = []

        # ── CSV 파일 헤더 초기화 ──────────────────────────────────────────────
        self._init_csv_files()

        logger.info("[텔레메트리] 서비스 초기화 완료. 인메모리 스냅샷 준비됨.")

    # ──────────────────────────────────────────────────────────────────────────
    # 퍼블릭 쓰기 인터페이스 (main.py 콜백에서 호출)
    # ──────────────────────────────────────────────────────────────────────────

    def record_env_telemetry(
        self,
        packet: PublishPacket,
        client_id: Optional[str] = None,
    ) -> None:
        """
        BME680 환경 PUBLISH 패킷을 처리하고 영속화합니다.

        실행 순서:
          1. _extract_sensor_fields() 로 모든 센서 필드 추출 및 타입 검증
          2. BME680 가스 저항값을 물리적 유효 범위로 검증 → gas_valid 결정
          3. self.telemetry_data 인메모리 스냅샷 원자적 갱신 (부분 패킷 carry-forward)
          4. 텔레메트리 CSV에 행 추가
          5. 모든 SSE 구독자 큐에 팬아웃

        추출되는 센서 필드:
          temp  (float, °C)  — BME680 온도
          hum   (float, %)   — BME680 상대 습도
          gas   (float, Ω)   — BME680 가스 저항값; [_GAS_MIN_OHM, _GAS_MAX_OHM]
                               범위를 벗어나거나 부재 시 gas_valid=False
          power (float, mW)  — 일부 펌웨어 버전에서만 포함되는 옵션 필드

        매개변수
        --------
        packet    : QoS 핸드셰이크가 완료된 PublishPacket.
        client_id : 세션 테이블에서 조회한 디바이스 ID (None이면 "unknown" 처리).
        """
        payload = packet.payload or {}

        # ── 1단계: 모든 센서 필드 안전 추출 및 타입 강제 변환 ─────────────────
        sensor = self._extract_sensor_fields(payload, packet.addr)

        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")

        # CSV/SSE/스냅샷에 공통으로 사용할 행 딕셔너리 구성
        row = {
            "timestamp":   timestamp,
            "client_id":   client_id or "unknown",
            "msg_id":      packet.msg_id,
            "qos":         packet.qos,
            "topic_id":    packet.topic_id,
            "addr_ip":     packet.addr[0],
            "addr_port":   packet.addr[1],
            # BME680 센서 측정값 (타입 안전 float 또는 None)
            "temp":        sensor["temp"],
            "hum":         sensor["hum"],
            "gas":         sensor["gas"],         # ← 가스 저항값 (Ω)
            "gas_valid":   sensor["gas_valid"],   # ← 유효성 플래그
            # 펌웨어 옵션 전력 필드
            "power":       sensor["power"],
            # 디버깅/재연용 원본 페이로드 보존
            "raw_payload": packet.payload_raw,
        }

        # ── 2단계: 인메모리 스냅샷 갱신 + CSV 기록 (락 내부에서 원자적 처리) ──
        with self._env_lock:
            # 공통 메타 필드 갱신
            self.telemetry_data["timestamp"]   = timestamp
            self.telemetry_data["client_id"]   = row["client_id"]
            self.telemetry_data["msg_id"]      = packet.msg_id
            self.telemetry_data["qos"]         = packet.qos
            self.telemetry_data["topic_id"]    = packet.topic_id
            self.telemetry_data["addr_ip"]     = packet.addr[0]
            self.telemetry_data["addr_port"]   = packet.addr[1]
            self.telemetry_data["raw_payload"] = packet.payload_raw
            self.telemetry_data["packet_count"] += 1

            # 센서 필드 carry-forward 갱신:
            # None이 들어온 경우(해당 키가 패킷에 없음) 이전 값을 그대로 보존합니다.
            # 부분 패킷(예: temp+hum만 전송)이 이전 gas 값을 지우지 않도록 방지합니다.
            if sensor["temp"]  is not None:
                self.telemetry_data["temp"]  = sensor["temp"]
            if sensor["hum"]   is not None:
                self.telemetry_data["hum"]   = sensor["hum"]
            if sensor["gas"]   is not None:
                self.telemetry_data["gas"]   = sensor["gas"]
            # gas_valid는 이번 패킷 결과로 항상 새로 결정합니다
            # (gas가 없는 패킷 → False, 있으면 범위 체크 결과)
            self.telemetry_data["gas_valid"] = sensor["gas_valid"]
            if sensor["power"] is not None:
                self.telemetry_data["power"] = sensor["power"]

            # CSV 파일에 행 추가 (락 보유 상태에서 호출)
            self._write_env_row(row)

        # ── 3단계: SSE 팬아웃 (락 외부에서 비차단 처리) ─────────────────────
        self._fanout_env(row)

        # ── 4단계: [2026-06] SW 전력 추정 ───────────────────────────────────
        # PublishPacket 페이로드에서 라이브 메트릭을 추출하여
        # IEEE Access 2024 경험적 공식으로 전력을 실시간 추정합니다.
        self._estimate_and_record_power(packet, client_id or "unknown")

        # ── 구조화 로그 출력 ──────────────────────────────────────────────────
        # gas 값을 kΩ 단위로 변환하고 유효성 태그([OK]/[INVALID])를 부여합니다
        if sensor["gas"] is not None:
            gas_log = (
                f"{sensor['gas'] / 1000:.1f} kOhm "
                f"[{'OK' if sensor['gas_valid'] else 'INVALID'}]"
            )
        else:
            gas_log = "미수신"

        logger.info(
            "[텔레메트리] ENV #%d | client='%s' msg_id=%d qos=%d | "
            "temp=%s hum=%s gas=%s power=%s",
            self.telemetry_data["packet_count"],
            row["client_id"], packet.msg_id, packet.qos,
            sensor["temp"], sensor["hum"], gas_log, sensor["power"],
        )

    def record_power_telemetry(self, packet: PowerPacket) -> None:
        """
        [DEPRECATED — 2026-06] INA219 하드웨어 계측 패킷 처리함수.

        Board 3 (INA219 전력 모니터)이 폐지되면서 이 메서드는 더 이상
        호출되지 않습니다. 하위 호환성을 위해 선언은 보존합니다.

        대체 추정 경로:
          record_env_telemetry() → _estimate_and_record_power()
          (PublishPacket 페이로드에서 rtt, retry, sleep_r 자동 추출후 추정)
        """
        logger.warning(
            "[텔레메트리] record_power_telemetry() 호출 — Board 3 하드웨어 거러 중단."
            " SW 추정 경로(_estimate_and_record_power)를 사용하세요."
        )

    def _estimate_and_record_power(
        self,
        packet: PublishPacket,
        client_id: str,
    ) -> None:
        """
        PublishPacket 페이로드에서 RTT/retry/sleep 메트릭을 추출하고
        IEEE Access 2024 공식으로 전력을 추정하여 power.csv에 기록합니다.

        실행 순서:
          1. 페이로드에서 rtt_ms, retry_count, sleep_mode_ratio 안전 추출
          2. estimate_energy() 호출 → estimated_energy_mwh 계산
          3. self.power_data 스냅샷 갱신
          4. power.csv에 행 추가
        """
        payload = packet.payload or {}
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")

        # ── 1단계: 소프트웨어 메트릭 안전 추출 ──────────────────────────────
        # "rtt", "retry", "sleep_r" 키로 응답이 없으면 기본값
        rtt_ms           = float(payload.get("rtt",     0.0))
        retry_count      = int(  payload.get("retry",   0))
        sleep_mode_ratio = float(payload.get("sleep_r", 0.0))
        packet_count     = int(  payload.get("pkt",     0))
        total_bytes      = int(  payload.get("bytes",   0))

        # ── 2단계: IEEE Access 2024 구식으로 에너지 추정 ──────────────────
        # base_current = (TX_MA × tx_ratio[qos]) + (RX_MA × rx_ratio[qos])
        # retry_penalty = 1 + (retry_count × 0.5)
        # estimated_energy_mwh = (base_current × rtt_ms × retry_penalty) / 3600000 × VCC
        estimated_energy_mwh = estimate_energy(
            qos=packet.qos,
            rtt_ms=rtt_ms,
            retry_count=retry_count,
        )

        row = {
            "timestamp":            timestamp,
            "client_id":            client_id,
            "qos":                  packet.qos,
            "rtt_ms":               rtt_ms,
            "retry_count":          retry_count,
            "sleep_mode_ratio":     sleep_mode_ratio,
            "estimated_energy_mwh": estimated_energy_mwh,
            "packet_count":         packet_count,
            "total_bytes":          total_bytes,
        }

        # ── 3단계: 인메모리 스냅샷 갱신 (락 내에서) ─────────────────────
        with self._power_lock:
            # client_id를 노드 키로 사용 (하드웨어 A/B 노드 구조 대체)
            if client_id not in self.power_data:
                from copy import deepcopy
                self.power_data[client_id] = deepcopy(_DEFAULT_POWER_NODE)

            snap = self.power_data[client_id]
            snap["timestamp"]            = timestamp
            snap["addr_ip"]              = packet.addr[0]
            snap["addr_port"]            = packet.addr[1]
            # 하드웨어 필드 (current_mA, voltage_V, power_mW)를
            # SW 추정 필드로 매핑하여 기존 REST API 호환성 유지
            snap["current_mA"]           = 0.0  # SW 추정에서는 실측 불가
            snap["voltage_V"]            = 0.0
            snap["power_mW"]             = estimated_energy_mwh * 3_600_000 / max(1.0, rtt_ms)
            snap["sample_count"]        += 1
            # 신규 필드 추가 (기존 타입에 없으면 자동 생성)
            snap["estimated_energy_mwh"] = estimated_energy_mwh
            snap["rtt_ms"]               = rtt_ms
            snap["retry_count"]          = retry_count
            snap["sleep_mode_ratio"]     = sleep_mode_ratio

            self._write_power_row(row)

        logger.info(
            "[\uc804\ub825\ucd94\uc815] client='%s' QoS=%d | RTT=%.2f ms | retry=%d | "
            "sleep=%.1f%% | energy=%.8f mWh",
            client_id, packet.qos, rtt_ms, retry_count,
            sleep_mode_ratio * 100.0, estimated_energy_mwh,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # 퍼블릭 조회 인터페이스 — 인메모리 (즉시 응답, 디스크 I/O 없음)
    # ──────────────────────────────────────────────────────────────────────────

    def get_latest_env(self) -> dict:
        """
        최신 환경 데이터 스냅샷을 반환합니다.

        첫 패킷이 아직 도착하지 않았어도 항상 모든 BME680 필드(gas 포함)가
        포함된 완전한 딕셔너리를 반환합니다 (기본값 0.0 / False).

        반환값
        ------
        dict — self.telemetry_data 의 얕은 복사본 (호출자가 변경해도 안전).
        """
        with self._env_lock:
            return dict(self.telemetry_data)

    def get_latest_power(self, node: Optional[str] = None) -> dict:
        """
        최신 전력 데이터 스냅샷을 반환합니다.

        매개변수
        --------
        node : "A", "B", 또는 None.
               None → 모든 노드 딕셔너리 반환.
               "A" / "B" → 해당 노드 단일 스냅샷 반환.

        반환값
        ------
        dict — 복사본 (호출자가 변경해도 안전).
        """
        with self._power_lock:
            if node is not None:
                key  = node.upper()
                snap = self.power_data.get(key, copy.deepcopy(_DEFAULT_POWER_NODE))
                return {"node": key, **dict(snap)}
            # node 미지정 시 전체 노드 딕셔너리 반환
            return {n: dict(s) for n, s in self.power_data.items()}

    # ──────────────────────────────────────────────────────────────────────────
    # 퍼블릭 조회 인터페이스 — CSV 기반 (이력 조회, 페이지네이션)
    # ──────────────────────────────────────────────────────────────────────────

    def get_recent_env(
        self,
        limit: int = 50,
        client_id: Optional[str] = None,
    ) -> List[dict]:
        """
        CSV에서 최근 `limit` 건의 환경 텔레메트리 행을 반환합니다.

        매개변수
        --------
        limit     : 최대 반환 행 수 (1~1000으로 제한).
        client_id : 디바이스 client_id 필터 (None이면 전체 반환).
        """
        limit = max(1, min(limit, 1000))
        rows  = _read_csv_tail(TELEMETRY_CSV, limit * 2 if client_id else limit)
        if client_id:
            rows = [r for r in rows if r.get("client_id") == client_id]
        return rows[-limit:]

    def get_recent_power(
        self,
        limit: int = 100,
        node: Optional[str] = None,
    ) -> List[dict]:
        """
        CSV에서 최근 `limit` 건의 전력 텔레메트리 행을 반환합니다.

        매개변수
        --------
        limit : 최대 반환 행 수 (1~1000으로 제한).
        node  : 노드 라벨 필터 — "A" 또는 "B" (None이면 전체 반환).
        """
        limit = max(1, min(limit, 1000))
        rows  = _read_csv_tail(POWER_CSV, limit * 2 if node else limit)
        if node:
            rows = [r for r in rows if r.get("node") == node.upper()]
        return rows[-limit:]

    def get_stats(self) -> dict:
        """
        저장된 텔레메트리 행 수와 실시간 패킷 카운터를 반환합니다.
        /api/diagnostics 엔드포인트에서 사용됩니다.
        """
        with self._env_lock:
            env_packets = self.telemetry_data["packet_count"]
        with self._power_lock:
            # 노드별 누적 샘플 수를 딕셔너리로 집계
            power_samples = {n: s["sample_count"] for n, s in self.power_data.items()}

        return {
            "env_rows":      _count_csv_rows(TELEMETRY_CSV),   # CSV 파일의 전체 행 수
            "power_rows":    _count_csv_rows(POWER_CSV),
            "env_packets":   env_packets,    # 인메모리 누적 환경 패킷 수
            "power_samples": power_samples,  # 인메모리 노드별 전력 샘플 수
        }

    # ──────────────────────────────────────────────────────────────────────────
    # SSE 구독 관리
    # ──────────────────────────────────────────────────────────────────────────

    def subscribe_env(self, maxsize: int = 100) -> queue.Queue:
        """
        실시간 환경 텔레메트리 SSE 구독자를 등록합니다.

        새 데이터가 도착할 때마다 반환된 Queue에 행 딕셔너리가 들어옵니다.
        HTTP 클라이언트 연결 종료 시 반드시 unsubscribe_env(q) 를 호출하세요.
        """
        q: queue.Queue = queue.Queue(maxsize=maxsize)
        with self._sse_lock:
            self._sse_subscribers.append(q)
        logger.debug(
            "[텔레메트리] SSE 구독자 추가됨. 현재 총 %d 명.", len(self._sse_subscribers)
        )
        return q

    def unsubscribe_env(self, q: queue.Queue) -> None:
        """이전에 등록된 SSE 구독자 큐를 제거합니다."""
        with self._sse_lock:
            try:
                self._sse_subscribers.remove(q)
                logger.debug(
                    "[텔레메트리] SSE 구독자 제거됨. 현재 총 %d 명.",
                    len(self._sse_subscribers),
                )
            except ValueError:
                pass  # 이미 제거된 큐 — 무시

    def _fanout_env(self, row: dict) -> None:
        """
        모든 SSE 구독자 큐에 행을 비차단 방식으로 전달합니다.
        큐가 가득 찬(느린) 클라이언트는 자동으로 제거됩니다.
        """
        with self._sse_lock:
            dead: List[queue.Queue] = []
            for q in self._sse_subscribers:
                try:
                    q.put_nowait(row)
                except queue.Full:
                    # 클라이언트가 너무 느림 → 제거 대상으로 표시
                    dead.append(q)
            for q in dead:
                self._sse_subscribers.remove(q)
                logger.warning("[텔레메트리] SSE 구독자 큐 포화 — 자동 제거됨.")

    # ──────────────────────────────────────────────────────────────────────────
    # 센서 필드 추출 (BME680 가스 센서 지원 포함)
    # ──────────────────────────────────────────────────────────────────────────

    def _extract_sensor_fields(self, payload: dict, addr) -> Dict[str, object]:
        """
        PUBLISH 페이로드 딕셔너리에서 BME680 센서 필드를 안전하게 추출하고
        타입을 강제 변환합니다.

        추출 규칙
        ─────────
        - 모든 수치 필드는 _coerce_float() 로 float으로 변환됩니다.
          "N/A" 문자열, 중첩 딕셔너리 등 변환 불가능한 타입은
          경고 로그를 출력하고 None을 반환합니다 (펌웨어 직렬화 버그 감지).
        - gas_valid 는 다음 두 조건이 모두 충족될 때만 True입니다:
            1. gas 가 None이 아님 (페이로드에 키 존재 및 float 변환 성공)
            2. gas 가 [_GAS_MIN_OHM, _GAS_MAX_OHM] 범위 내에 있음
          gas 저항이 1 kΩ 미만이면 센서 히터가 아직 예열 중임을 의미합니다.

        매개변수
        --------
        payload : PUBLISH 패킷 바디에서 파싱된 JSON 딕셔너리.
        addr    : 송신자 (ip, port) 튜플 — 경고 메시지에만 사용.

        반환값
        ------
        dict — 키: temp, hum, gas, gas_valid, power
        """
        # 각 센서 필드를 float으로 안전하게 변환 (변환 실패 시 None 반환)
        temp  = _coerce_float(payload.get("temp"),  field="temp",  addr=addr)
        hum   = _coerce_float(payload.get("hum"),   field="hum",   addr=addr)
        gas   = _coerce_float(payload.get("gas"),   field="gas",   addr=addr)
        power = _coerce_float(payload.get("power"), field="power", addr=addr)

        # BME680 가스 저항 유효 범위 검증
        gas_valid: bool = (
            gas is not None
            and self._GAS_MIN_OHM <= gas <= self._GAS_MAX_OHM
        )

        # 값이 존재하지만 범위를 벗어난 경우 경고 로그 출력
        if gas is not None and not gas_valid:
            logger.warning(
                "[텔레메트리] BME680 gas=%.1f Ohm (%s) 가 유효 범위 "
                "[%.0f Ohm, %.0f Ohm] 밖입니다 — gas_valid=False.",
                gas, addr, self._GAS_MIN_OHM, self._GAS_MAX_OHM,
            )

        return {
            "temp":      temp,
            "hum":       hum,
            "gas":       gas,         # BME680 가스 저항값 (Ω) — 핵심 신규 필드
            "gas_valid": gas_valid,   # 물리적 유효성 플래그
            "power":     power,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # 저장소 어댑터 (여기서만 교체하면 호출부 수정 불필요)
    # ──────────────────────────────────────────────────────────────────────────

    def _write_env_row(self, row: dict) -> None:
        """환경 텔레메트리 행 1개를 CSV에 추가합니다. (호출자가 락을 보유 중)"""
        try:
            with open(TELEMETRY_CSV, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f, fieldnames=self._ENV_FIELDS, extrasaction="ignore"
                )
                writer.writerow(row)
        except OSError as exc:
            logger.error("[텔레메트리] 환경 CSV 쓰기 실패: %s", exc)

    def _write_power_row(self, row: dict) -> None:
        """전력 텔레메트리 행 1개를 CSV에 추가합니다. (호출자가 락을 보유 중)"""
        try:
            with open(POWER_CSV, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f, fieldnames=self._POWER_FIELDS, extrasaction="ignore"
                )
                writer.writerow(row)
        except OSError as exc:
            logger.error("[텔레메트리] 전력 CSV 쓰기 실패: %s", exc)

    # ──────────────────────────────────────────────────────────────────────────
    # CSV 헤더 초기화
    # ──────────────────────────────────────────────────────────────────────────

    def _init_csv_files(self) -> None:
        """디스크에 아직 존재하지 않는 CSV 파일에 헤더를 기록합니다."""
        for path, fields in [
            (TELEMETRY_CSV, self._ENV_FIELDS),
            (POWER_CSV,     self._POWER_FIELDS),
        ]:
            if not os.path.exists(path):
                try:
                    with open(path, "w", newline="", encoding="utf-8") as f:
                        csv.DictWriter(f, fieldnames=fields).writeheader()
                    logger.info("[텔레메트리] 로그 파일 생성됨: %s", path)
                except OSError as exc:
                    logger.error("[텔레메트리] %s 생성 실패: %s", path, exc)


# ──────────────────────────────────────────────────────────────────────────────
# 모듈 레벨 유틸리티 함수 (클래스 상태 없음 — 순수 함수)
# ──────────────────────────────────────────────────────────────────────────────

def _read_csv_tail(path: str, n: int) -> List[dict]:
    """
    CSV 파일 전체를 메모리에 올리지 않고 마지막 N행을 읽어 반환합니다.
    각 행은 (필드명 → 값 문자열) 딕셔너리로 반환됩니다.
    """
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", newline="", encoding="utf-8") as f:
            reader = list(csv.DictReader(f))
            return reader[-n:]
    except OSError as exc:
        logger.error("[텔레메트리] CSV 읽기 실패 %s: %s", path, exc)
        return []


def _count_csv_rows(path: str) -> int:
    """CSV 파일의 데이터 행 수를 반환합니다 (헤더 제외). 파일 없으면 0."""
    if not os.path.exists(path):
        return 0
    try:
        with open(path, "r", newline="", encoding="utf-8") as f:
            # 헤더 행 1개를 빼서 실제 데이터 행 수만 계산
            return max(0, sum(1 for _ in f) - 1)
    except OSError:
        return 0


def _coerce_float(
    value: object,
    field: str = "",
    addr: object = None,
) -> Optional[float]:
    """
    JSON 페이로드 값을 Python float으로 안전하게 변환합니다.

    반환값
    ------
    float  : 값이 이미 수치형이거나 float으로 파싱 가능한 문자열인 경우.
    None   : 값이 None인 경우 (페이로드에 해당 키 없음).

    값이 존재하지만 변환 불가능한 경우 (예: "N/A", 중첩 딕셔너리, 리스트)
    WARNING을 출력하고 None을 반환합니다 → 펌웨어 JSON 직렬화 버그 감지 용도.
    """
    if value is None:
        # 키가 페이로드에 없는 정상 케이스 — 조용히 None 반환
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        logger.warning(
            "[텔레메트리] '%s' 필드를 float으로 변환 불가 (수신값: %r, 송신지: %s) "
            "-- None으로 저장. 펌웨어 JSON 직렬화를 확인하세요.",
            field, value, addr,
        )
        return None
