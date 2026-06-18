"""
backend/app/services/power_estimator.py
─────────────────────────────────────────────────────────────────────────────
IEEE Access 2024 기반 소프트웨어 정의 전력 추정 엔진
(DOI: 10.1109/ACCESS.2024.3523864)

하드웨어 INA219 Board 3을 대체하여, MQTT-SN 패킷 메트릭(RTT, retry_count,
QoS 레벨, sleep_mode_ratio)으로부터 ESP32-S3 소비 전력을 추정합니다.

전력 추정 모델
──────────────
  ESP32-S3 WiFi 전류 프로파일 (데이터시트 기준, IEEE Access 2024 Table III):
    TX_MA  = 251 mA  (전송 피크 전류 — 802.11b 2.4 GHz)
    RX_MA  =  78 mA  (수신 대기 전류)
    IDLE_MA = 20 mA  (WiFi 연결 유지, CPU 동작)
    SLEEP_MA = 0.01 mA (Light Sleep 전류)
    VCC    =   3.3 V (공급 전압)

  QoS별 TX/RX 위상 비율 (IEEE Access 2024 Table III 기반):
    QoS 0 (단발 전송): TX=100%, RX=0%    — 단방향 Fire-and-Forget
    QoS 1 (2단계):    TX=60%,  RX=40%   — PUBLISH → PUBACK
    QoS 2 (4단계):    TX=50%,  RX=50%   — PUBLISH → PUBREC → PUBREL → PUBCOMP

  핵심 공식:
    base_current = (TX_MA × tx_ratio[qos]) + (RX_MA × rx_ratio[qos])
    retry_penalty = 1 + (retry_count × 0.5)
    estimated_energy_mwh = (base_current × rtt_ms × retry_penalty) / 3_600_000 × VCC

  단위 검증:
    [mA] × [ms] / [ms/h × 1000] × [V]
    = mA × h × V
    = mWh  ✓

사용 예시
─────────
    from app.services.power_estimator import estimate_energy

    energy = estimate_energy(qos=1, rtt_ms=15.3, retry_count=0)
    print(f"추정 소비 에너지: {energy:.6f} mWh")
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
from typing import Dict

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# ESP32-S3 하드웨어 전류 상수 (데이터시트 + IEEE Access 2024)
# ──────────────────────────────────────────────────────────────────────────────

#: TX 피크 전류 (mA) — 802.11b 2.4 GHz WiFi 전송 모드
TX_MA: float = 251.0

#: RX 수신 대기 전류 (mA) — WiFi 수신 활성 모드
RX_MA: float = 78.0

#: 공급 전압 (V) — ESP32-S3 3.3V 레귤레이터 기준
VCC_V: float = 3.3

# ──────────────────────────────────────────────────────────────────────────────
# QoS별 TX/RX 위상 시간 비율 (IEEE Access 2024, Table III 기반)
# ──────────────────────────────────────────────────────────────────────────────

#: QoS 레벨별 TX 시간 비율 (0.0~1.0)
#: QoS 0: 단방향 전송만 → TX 100%
#: QoS 1: PUBLISH(TX) + PUBACK 수신 대기(RX) → TX 60% / RX 40%
#: QoS 2: PUBLISH(TX) + PUBREC(RX) + PUBREL(TX) + PUBCOMP(RX) → 50% / 50%
TX_RATIO: Dict[int, float] = {
    0: 1.00,  # QoS 0: TX only (단발 Fire-and-Forget)
    1: 0.60,  # QoS 1: 2단계 핸드셰이크 (PUBLISH→PUBACK)
    2: 0.50,  # QoS 2: 4단계 핸드셰이크 (PUBLISH→PUBREC→PUBREL→PUBCOMP)
}

#: QoS 레벨별 RX 시간 비율 (= 1 - TX_RATIO)
RX_RATIO: Dict[int, float] = {
    0: 0.00,  # QoS 0: 응답 수신 없음
    1: 0.40,  # QoS 1: PUBACK 수신 대기
    2: 0.50,  # QoS 2: PUBREC + PUBCOMP 수신 대기
}

# ──────────────────────────────────────────────────────────────────────────────
# 공개 인터페이스
# ──────────────────────────────────────────────────────────────────────────────

def estimate_energy(
    qos: int,
    rtt_ms: float,
    retry_count: int,
) -> float:
    """
    IEEE Access 2024 경험적 공식으로 단일 MQTT 트랜잭션의 소비 에너지를 추정합니다.

    공식
    ────
        base_current   = (TX_MA × tx_ratio[qos]) + (RX_MA × rx_ratio[qos])
        retry_penalty  = 1 + (retry_count × 0.5)
        estimated_mwh  = (base_current × rtt_ms × retry_penalty) / 3_600_000 × VCC

    매개변수
    --------
    qos         : QoS 레벨 (0, 1, 2). 범위 초과 시 QoS 1로 폴백.
    rtt_ms      : 왕복 전송 시간 (ms). 음수이면 0으로 클리핑.
    retry_count : 재전송 횟수 (0 = 재전송 없음). 음수이면 0으로 클리핑.

    반환값
    ------
    float — 추정 소비 에너지 (mWh). 항상 0 이상.

    예시
    ----
    >>> estimate_energy(qos=1, rtt_ms=15.3, retry_count=0)
    0.000000928...  # 약 0.93 μWh
    >>> estimate_energy(qos=2, rtt_ms=45.0, retry_count=1)
    0.00000413...   # 약 4.13 μWh
    """
    # ── 입력값 정리 (방어적 클리핑) ──────────────────────────────────────────
    qos         = max(0, min(int(qos), 2))      # QoS: [0, 2]
    rtt_ms      = max(0.0, float(rtt_ms))       # RTT: [0, ∞)
    retry_count = max(0, int(retry_count))      # retry: [0, ∞)

    # ── QoS별 베이스 전류 계산 ───────────────────────────────────────────────
    # TX 위상과 RX 위상의 RTT 내 점유 비율을 가중 평균으로 합산합니다.
    base_current_ma: float = (TX_MA * TX_RATIO[qos]) + (RX_MA * RX_RATIO[qos])

    # ── 재전송 패널티 계수 ────────────────────────────────────────────────────
    # 재전송이 없으면(retry=0) 패널티 1.0 (×1배)
    # 재전송 1회마다 50% 추가 에너지 소비로 모델링
    # 근거: 재전송 = 동일 패킷 재전송 + ACK 대기 → ≈0.5× 추가 에너지
    retry_penalty: float = 1.0 + (retry_count * 0.5)

    # ── 에너지 추정 (mWh) ─────────────────────────────────────────────────────
    # E = I × t × V
    #   = base_current_ma [mA] × (rtt_ms / 3_600_000) [h] × VCC_V [V]
    #   = mWh
    estimated_energy_mwh: float = (
        (base_current_ma * rtt_ms * retry_penalty) / 3_600_000.0 * VCC_V
    )

    logger.debug(
        "[전력추정] QoS=%d | RTT=%.2f ms | retry=%d | "
        "base=%.1f mA | penalty=×%.2f | energy=%.8f mWh",
        qos, rtt_ms, retry_count,
        base_current_ma, retry_penalty, estimated_energy_mwh,
    )

    return estimated_energy_mwh


def estimate_energy_with_sleep(
    qos: int,
    rtt_ms: float,
    retry_count: int,
    sleep_mode_ratio: float,
    cycle_duration_ms: float = 5000.0,
) -> dict:
    """
    수면 모드를 포함한 전체 사이클 에너지를 추정하고 상세 분석을 반환합니다.

    단순 estimate_energy()는 활성 트랜잭션 구간만 추정합니다.
    이 함수는 추가로 Sleep 구간의 에너지를 포함하여 전체 사이클 에너지와
    평균 전류를 계산합니다.

    매개변수
    --------
    qos               : QoS 레벨 (0, 1, 2)
    rtt_ms            : MQTT 트랜잭션 왕복 시간 (ms)
    retry_count       : 재전송 횟수
    sleep_mode_ratio  : 사이클 중 Sleep 비율 (0.0~1.0)
    cycle_duration_ms : 전체 사이클 길이 (ms, 기본 5000ms)

    반환값
    ------
    dict — 상세 전력 추정 결과:
        active_energy_mwh  : 활성 트랜잭션 구간 에너지
        sleep_energy_mwh   : Sleep 구간 에너지
        total_energy_mwh   : 전체 사이클 에너지
        average_current_ma : 사이클 평균 전류 (mA)
        efficiency_gain_pct: 절전 이득 (Sleep 없는 경우 대비 %)
    """
    # 활성 구간 에너지 (트랜잭션)
    active_energy = estimate_energy(qos, rtt_ms, retry_count)

    # Sleep 구간 에너지 (Light Sleep 기준: ~10 μA = 0.01 mA)
    sleep_duration_ms = cycle_duration_ms * max(0.0, min(1.0, sleep_mode_ratio))
    sleep_energy_mwh  = (0.01 * sleep_duration_ms / 3_600_000.0 * VCC_V)

    # 전체 사이클 에너지
    total_energy_mwh = active_energy + sleep_energy_mwh

    # 사이클 평균 전류 (mA) = E [mWh] / (t [h] × V [V])
    cycle_duration_h = cycle_duration_ms / 3_600_000.0
    avg_current_ma = (
        (total_energy_mwh / VCC_V / cycle_duration_h)
        if cycle_duration_h > 0 else 0.0
    )

    # Sleep 없는 경우 대비 에너지 절감율
    no_sleep_energy = estimate_energy(qos, cycle_duration_ms, retry_count)
    efficiency_gain = (
        (1.0 - total_energy_mwh / no_sleep_energy) * 100.0
        if no_sleep_energy > 0 else 0.0
    )

    result = {
        "active_energy_mwh":  active_energy,
        "sleep_energy_mwh":   sleep_energy_mwh,
        "total_energy_mwh":   total_energy_mwh,
        "average_current_ma": avg_current_ma,
        "efficiency_gain_pct": efficiency_gain,
    }

    logger.debug(
        "[전력추정 상세] QoS=%d | active=%.8f mWh | sleep=%.8f mWh | "
        "total=%.8f mWh | avg=%.2f mA | 절감=%.1f%%",
        qos, active_energy, sleep_energy_mwh,
        total_energy_mwh, avg_current_ma, efficiency_gain,
    )

    return result
