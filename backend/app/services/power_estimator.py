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

  사이클 전체 에너지 (estimate_cycle_energy):
    total = 트랜잭션(RTT 구간) + 대기(active_ms - rtt_ms, IDLE_MA) + Sleep(sleep_ms, SLEEP_MA)
    estimate_energy()는 트랜잭션 구간만 계산하므로, Sleep/대기 시간이 다른 두 노드를
    비교할 때는 반드시 estimate_cycle_energy()를 사용해야 합니다.

  주의: TX/RX 비율과 IDLE_MA, SLEEP_MA는 가정값입니다. 절대값이 아닌 상대 비교용이며,
        실측(INA226 등)으로 보정 전에는 "추정값"으로 표기해야 합니다.

사용 예시
─────────
    from app.services.power_estimator import estimate_energy, estimate_cycle_energy

    energy = estimate_energy(qos=1, rtt_ms=15.3, retry_count=0)
    print(f"트랜잭션 에너지: {energy:.6f} mWh")

    cycle = estimate_cycle_energy(qos=1, rtt_ms=15.3, retry_count=0,
                                  active_ms=120.0, sleep_ms=5000.0)
    print(f"사이클 에너지: {cycle['total_energy_mwh']:.6f} mWh")
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

#: WiFi 연결 유지 + CPU 동작 전류 (mA) — 송수신 없이 깨어 있는 구간
#: 주의: 데이터시트 기반 가정값입니다. 실측(INA226 등)으로 보정하는 것을 권장합니다.
IDLE_MA: float = 20.0

#: Light Sleep 전류 (mA)
SLEEP_MA: float = 0.01

#: 공급 전압 (V) — ESP32-S3 3.3V 레귤레이터 기준
VCC_V: float = 3.3

#: 재전송 1회당 추가 에너지 계수: retry_penalty = 1 + retry_count × RETRY_PENALTY
#: 가정값입니다 (재전송 = 동일 패킷 재전송 + ACK 대기 ≈ 0.5× 추가).
RETRY_PENALTY: float = 0.5

#: ACK 대기 타임아웃 (ms) — 두 펌웨어 모두 2000 ms (Gingerbread wait_for_packet,
#: Standard ACK_TIMEOUT_MS). 재전송 1회마다 이 시간만큼 응답을 "기다린" 뒤 다시 보냅니다.
ACK_TIMEOUT_MS: float = 2000.0

#: mA × ms → mAh 변환 분모 (1 h = 3,600,000 ms)
_MS_PER_HOUR: float = 3_600_000.0

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
    0.00254974...   # 약 2.55 μWh
    >>> estimate_energy(qos=2, rtt_ms=45.0, retry_count=1)
    0.01017843...   # 약 10.18 μWh
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
    retry_penalty: float = 1.0 + (retry_count * RETRY_PENALTY)

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


def estimate_cycle_energy(
    qos: int,
    rtt_ms: float,
    retry_count: int,
    active_ms: float,
    sleep_ms: float,
    timeout_wait_ms: float = 0.0,
) -> dict:
    """
    한 사이클(활성 구간 + Sleep 구간) 전체의 소비 에너지를 추정합니다.

    사이클을 세 구간으로 나누어 각각의 전류로 계산합니다.

        1. 트랜잭션 구간 (rtt_ms - timeout_wait_ms) : QoS별 TX/RX 가중 전류 × 재전송 패널티
        2. 대기 구간 (active_ms - 트랜잭션)          : IDLE_MA (송수신 없이 기다리는 시간)
        3. Sleep 구간 (sleep_ms)                     : SLEEP_MA

        total = 트랜잭션 + 대기 + Sleep

    재전송의 이중 계산 방지:
        rtt_ms는 "최초 전송 ~ ACK 수신"이라 재전송 타임아웃 대기 시간을 이미 포함합니다.
        이 대기 시간(timeout_wait_ms)에 TX/RX 전류를 적용하고 다시 재전송 패널티를 곱하면
        같은 시간을 두 번 세게 됩니다. 타임아웃 동안은 패킷을 보내는 것이 아니라 응답을
        기다리는 시간이므로, 트랜잭션 구간에서 빼서 대기 구간(IDLE_MA)으로 계산합니다.
        재전송 자체의 추가 전송 비용은 RETRY_PENALTY가 트랜잭션 구간에 반영합니다.
        (rtt_ms 자체는 지연시간 지표로 그대로 보고되며 바뀌지 않습니다.)

    절전 이득(efficiency_gain_pct)은 "동일 사이클에서 Sleep 없이 무선을 계속 켜 둔
    경우"(Sleep 구간도 IDLE_MA로 소비)와 비교한 값입니다.

    매개변수
    --------
    qos         : QoS 레벨 (0, 1, 2)
    rtt_ms      : 트랜잭션 왕복 시간 (ms)
    retry_count : 재전송 횟수
    active_ms   : 이번 사이클의 활성(깨어 있는) 시간 (ms). rtt_ms보다 작으면 rtt_ms로 보정.
    sleep_ms    : 이번 사이클의 Sleep 시간 (ms)
    timeout_wait_ms : rtt_ms 중 재전송 타임아웃 대기에 쓰인 시간 (ms). rtt_ms를 넘지 않게 보정.
                      보통 retry_count × ACK_TIMEOUT_MS.

    반환값
    ------
    dict:
        active_energy_mwh   : 트랜잭션 구간 에너지
        idle_energy_mwh     : 대기 구간 에너지
        sleep_energy_mwh    : Sleep 구간 에너지
        total_energy_mwh    : 사이클 전체 에너지
        average_current_ma  : 사이클 평균 전류 (mA)
        efficiency_gain_pct : 절전 이득 (Sleep 없는 경우 대비 %)
    """
    rtt_ms      = max(0.0, float(rtt_ms))
    wait_ms     = min(rtt_ms, max(0.0, float(timeout_wait_ms)))  # 타임아웃 대기 ⊂ RTT
    exchange_ms = rtt_ms - wait_ms                               # 실제 송수신이 일어난 구간
    active_ms   = max(rtt_ms, float(active_ms))  # 활성 구간은 RTT(대기 포함)보다 짧을 수 없음
    sleep_ms    = max(0.0, float(sleep_ms))
    idle_ms     = active_ms - exchange_ms                        # 타임아웃 대기 포함
    cycle_ms    = active_ms + sleep_ms

    active_energy = estimate_energy(qos, exchange_ms, retry_count)
    idle_energy   = IDLE_MA  * idle_ms  / _MS_PER_HOUR * VCC_V
    sleep_energy  = SLEEP_MA * sleep_ms / _MS_PER_HOUR * VCC_V
    total_energy  = active_energy + idle_energy + sleep_energy

    # 평균 전류 (mA) = E [mWh] / (t [h] × V [V])
    cycle_h = cycle_ms / _MS_PER_HOUR
    avg_current_ma = (total_energy / VCC_V / cycle_h) if cycle_h > 0 else 0.0

    # 비교 기준: Sleep 구간에도 무선을 켜 둔 경우 (Sleep 시간을 IDLE_MA로 소비)
    no_sleep_energy = active_energy + IDLE_MA * (cycle_ms - exchange_ms) / _MS_PER_HOUR * VCC_V
    efficiency_gain = (
        (1.0 - total_energy / no_sleep_energy) * 100.0
        if no_sleep_energy > 0 else 0.0
    )

    logger.debug(
        "[전력추정 사이클] QoS=%d | active=%.8f | idle=%.8f | sleep=%.8f | "
        "total=%.8f mWh | avg=%.2f mA | 절감=%.1f%%",
        int(qos), active_energy, idle_energy, sleep_energy,
        total_energy, avg_current_ma, efficiency_gain,
    )

    return {
        "active_energy_mwh":   active_energy,
        "idle_energy_mwh":     idle_energy,
        "sleep_energy_mwh":    sleep_energy,
        "total_energy_mwh":    total_energy,
        "average_current_ma":  avg_current_ma,
        "efficiency_gain_pct": efficiency_gain,
    }


def estimate_energy_with_sleep(
    qos: int,
    rtt_ms: float,
    retry_count: int,
    sleep_mode_ratio: float,
    cycle_duration_ms: float = 5000.0,
) -> dict:
    """
    Sleep 비율로 사이클을 나누어 estimate_cycle_energy()에 위임합니다.

    사이클별 활성/Sleep 시간을 직접 알 수 있으면 estimate_cycle_energy()를 쓰세요.
    이 함수는 (Sleep 비율, 사이클 길이)만 알 때의 편의 래퍼입니다.

    매개변수
    --------
    sleep_mode_ratio  : 사이클 중 Sleep 비율 (0.0~1.0)
    cycle_duration_ms : 전체 사이클 길이 (ms, 기본 5000ms)
    """
    cycle_ms = max(0.0, float(cycle_duration_ms))
    sleep_ms = cycle_ms * max(0.0, min(1.0, float(sleep_mode_ratio)))
    return estimate_cycle_energy(
        qos=qos,
        rtt_ms=rtt_ms,
        retry_count=retry_count,
        active_ms=cycle_ms - sleep_ms,
        sleep_ms=sleep_ms,
    )
