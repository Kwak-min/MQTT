"""
backend/tools/power_sensitivity.py
─────────────────────────────────────────────────────────────────────────────
전력 추정 모델의 민감도 분석.

이 프로젝트의 전력값은 실측이 아니라 "가정한 상수"로 계산한 추정값입니다.
따라서 결론(예: Gingerbread가 Standard보다 X배 절전)이 상수 선택에 얼마나
좌우되는지 보여줘야 합니다. 이 스크립트는 다음을 출력합니다.

  표 1. IDLE_MA를 바꿔 가며, Gingerbread의 Sleep을 두 가지로 해석했을 때의 절전 배율
        A) 진짜 Sleep     : Sleep 구간 = SLEEP_MA (0.01 mA)
        B) delay() 대기   : 현재 펌웨어의 실제 동작. 무선이 켜져 있어 Sleep 구간도 IDLE_MA
  표 2. 재전송 패널티(RETRY_PENALTY)와 재전송 횟수에 따른 Standard 노드 에너지 변화

실행 (backend 폴더에서):
    python tools/power_sensitivity.py

배율은 "평균 전류" 기준입니다. Gingerbread 사이클(활성+Sleep)과 Standard 사이클
길이가 다르기 때문에 사이클당 에너지가 아닌 평균 전류로 비교해야 공정합니다.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import contextmanager

# `python tools/power_sensitivity.py` 로 실행해도 app 패키지를 찾도록 backend/를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.services.power_estimator as pe  # noqa: E402

logging.disable(logging.CRITICAL)

# ── 시나리오 상수 (펌웨어 실제 값) ───────────────────────────────────────────
STANDARD_CYCLE_MS = 5000.0     # main_standard_MQTT.cpp: PUBLISH_INTERVAL_MS (무선 항상 켜짐)
GINGERBREAD_ACTIVE_MS = 120.0  # 예시 활성 시간 (센싱+추론+핸드셰이크). 실측 로그로 교체 권장
GINGERBREAD_SLEEP_MS = 5000.0  # main_gingerbread.cpp: SLEEP_DURATION_MS
RTT_MS = 40.0                  # 예시 RTT. 실측 로그로 교체 권장


@contextmanager
def override(**consts):
    """power_estimator 모듈 상수를 임시로 바꾸고 종료 시 원복합니다."""
    saved = {k: getattr(pe, k) for k in consts}
    try:
        for k, v in consts.items():
            setattr(pe, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(pe, k, v)


def standard(qos=1, rtt=RTT_MS, retry=0):
    return pe.estimate_cycle_energy(
        qos, rtt, retry, active_ms=STANDARD_CYCLE_MS, sleep_ms=0.0,
        timeout_wait_ms=retry * pe.ACK_TIMEOUT_MS,
    )


def gingerbread(qos=1, rtt=RTT_MS, retry=0, real_sleep=True):
    if real_sleep:   # A) Sleep 구간을 SLEEP_MA로 계산
        return pe.estimate_cycle_energy(
            qos, rtt, retry, active_ms=GINGERBREAD_ACTIVE_MS, sleep_ms=GINGERBREAD_SLEEP_MS,
            timeout_wait_ms=retry * pe.ACK_TIMEOUT_MS,
        )
    # B) delay()로 대기: 무선이 켜져 있으므로 Sleep 구간도 활성(=IDLE_MA)으로 계산
    return pe.estimate_cycle_energy(
        qos, rtt, retry, active_ms=GINGERBREAD_ACTIVE_MS + GINGERBREAD_SLEEP_MS, sleep_ms=0.0,
        timeout_wait_ms=retry * pe.ACK_TIMEOUT_MS,
    )


def table_idle_sweep() -> None:
    print("=" * 78)
    print("표 1. IDLE_MA 민감도 — Standard 대비 Gingerbread 절전 배율 (평균 전류 기준, QoS 1)")
    print("      배율 = Standard 평균전류 / Gingerbread 평균전류  (클수록 Gingerbread가 유리)")
    print("=" * 78)
    print(f"{'IDLE_MA':>8} | {'Standard(mA)':>12} | {'A) 진짜 Sleep':>22} | {'B) delay() 대기':>22}")
    print(f"{'':>8} | {'':>12} | {'평균mA':>9} {'배율':>10} | {'평균mA':>9} {'배율':>10}")
    print("-" * 78)
    for idle in (10.0, 20.0, 50.0, 80.0, 100.0):
        with override(IDLE_MA=idle):
            s = standard()["average_current_ma"]
            a = gingerbread(real_sleep=True)["average_current_ma"]
            b = gingerbread(real_sleep=False)["average_current_ma"]
        print(f"{idle:>8.0f} | {s:>12.2f} | {a:>9.3f} {s / a:>9.1f}x | {b:>9.2f} {s / b:>9.2f}x")
    print()
    print("해석: A는 'Gingerbread가 실제로 Sleep에 들어간다'는 가정입니다. 현재 펌웨어는 delay()로")
    print("      대기하므로 실제 동작은 B에 가깝고, 그 경우 두 노드의 차이는 사라집니다.")
    print("      즉 절전 배율은 시스템의 성능이 아니라 Sleep 구현 여부에 좌우됩니다.")
    print()


def table_retry_sweep() -> None:
    print("=" * 78)
    print("표 2. 재전송 민감도 — Standard 노드 사이클 에너지 (QoS 1, RTT에 타임아웃 2000 ms/회 포함)")
    print("      값 = 사이클 에너지 (mWh),  괄호 = 재전송 0회 대비 배수")
    print("=" * 78)
    penalties = (0.0, 0.25, 0.5, 1.0)
    print(f"{'재전송':>6} | " + " | ".join(f"RETRY_PENALTY={p:<4}" for p in penalties))
    print("-" * 78)
    base = {}
    for p in penalties:
        with override(RETRY_PENALTY=p):
            base[p] = standard(retry=0)["total_energy_mwh"]
    for retry in (0, 1, 2, 3):
        cells = []
        for p in penalties:
            with override(RETRY_PENALTY=p):
                e = standard(rtt=RTT_MS + retry * pe.ACK_TIMEOUT_MS, retry=retry)["total_energy_mwh"]
            cells.append(f"{e:8.4f} (x{e / base[p]:.2f})  ")
        print(f"{retry:>6} | " + " | ".join(cells))
    print()
    print("해석: 재전송 대기를 IDLE_MA로 계산하므로 재전송 1~2회의 추가 비용은 작고,")
    print("      RETRY_PENALTY 가정에 따라 달라지는 폭도 작습니다.")
    print("주의: 재전송 3회 행은 RTT(약 6040 ms)가 5000 ms 사이클을 넘어 사이클 자체가 길어진")
    print("      효과(더 오래 깨어 있음)가 포함됩니다. RETRY_PENALTY=0 열에서도 값이 오르는 이유입니다.")
    print()


def main() -> None:
    print(f"가정: Standard 사이클 {STANDARD_CYCLE_MS:.0f} ms 항상 활성 | "
          f"Gingerbread 활성 {GINGERBREAD_ACTIVE_MS:.0f} ms + Sleep {GINGERBREAD_SLEEP_MS:.0f} ms | "
          f"RTT {RTT_MS:.0f} ms")
    print(f"현재 상수: TX={pe.TX_MA} RX={pe.RX_MA} IDLE={pe.IDLE_MA} SLEEP={pe.SLEEP_MA} mA, "
          f"RETRY_PENALTY={pe.RETRY_PENALTY}\n")
    table_idle_sweep()
    table_retry_sweep()


if __name__ == "__main__":
    main()
