#pragma once
/*
 * firmware/include/gas_baseline.h
 * ─────────────────────────────────────────────────────────────────────────────
 * BME680 가스 저항의 "기준값 대비 비율" 추적기. 표준 C++만 사용하므로 PC에서도 컴파일됩니다.
 *
 * 왜 필요한가
 *   가스 저항의 절대값(kΩ)은 센서 개체·예열 시간·시간 경과·환경에 따라 정상 상태의 기준이 크게
 *   달라집니다(실험에서 세션마다 60~160 kΩ). 절대값으로 위험을 판단하면 새 환경에서 무너지므로,
 *   "지금 값이 그 센서의 깨끗한 상태 대비 얼마나 떨어졌나"를 비율로 씁니다.
 *
 *   ratio = 현재값 / 기준값      (0 초과 1 이하. 1이면 기준 상태, 값이 작을수록 가스 증가)
 *   기준값 = max(현재값, 이전 기준값 × (1 - DECAY))
 *     · 값이 올라가면 기준값도 즉시 따라 올라감 (예열 중 저항이 상승하는 것을 추적)
 *     · 값이 내려가면(가스 발생) 기준값은 거의 유지됨 → 비율이 떨어져 이상을 감지
 *     · 아주 느리게 감쇠(반감기 약 6시간)하여 센서 노화나 환경 변화에 적응
 *
 * 동작 규칙 (ml_model/gas_baseline.py가 똑같이 재현하며, verify_export.py가 g++로 대조합니다)
 *   · 샘플 단위로 갱신합니다 (시간이 아니라 호출 횟수). 수집(5초 주기)과 배포(약 5초 주기)가 같아야 합니다.
 *   · 처음 WARMUP 샘플 동안은 항상 1.0을 반환합니다 (기준값이 아직 믿을 수 없어 오경보를 막음).
 *   · 부팅하면 상태가 초기화됩니다 (기준값을 저장하지 않음).
 *   · 유효하지 않은 값(0 이하)은 상태를 바꾸지 않고 1.0을 반환합니다.
 *
 * 한계
 *   · 가스가 아주 오래(수 시간) 지속되면 기준값이 감쇠하여 비율이 다시 1에 가까워집니다.
 *   · 첫 WARMUP 샘플(약 10분) 동안은 가스 이상을 감지하지 못합니다 (온도/습도는 정상 동작).
 * ─────────────────────────────────────────────────────────────────────────────
 */

#include <stdint.h>

// 이 두 값은 ml_model/gas_baseline.py가 이 파일에서 읽어 학습에 씁니다 (한 곳에서만 정의).
#define GAS_BASELINE_WARMUP_SAMPLES 120      // 5초 주기 기준 약 10분
#define GAS_BASELINE_DECAY 0.00016f          // 샘플당 감쇠율. 반감기 ≈ ln2/0.00016 ≈ 4330샘플 ≈ 6시간

struct GasBaseline {
  float    baseline;   // 현재 기준값 (kΩ)
  uint32_t n;          // 지금까지 반영한 유효 샘플 수
};

static inline void gas_baseline_reset(GasBaseline &b) {
  b.baseline = 0.0f;
  b.n = 0;
}

// 새 가스 저항(kΩ)을 반영하고 기준값 대비 비율을 반환합니다.
static inline float gas_baseline_update(GasBaseline &b, float gas_kohm) {
  if (!(gas_kohm > 0.0f)) {
    return 1.0f;                               // 유효하지 않은 값(0 이하, NaN): 상태 유지, 중립값
  }
  if (b.n < 0xFFFFFFFFu) {
    b.n++;
  }
  if (b.n == 1) {
    b.baseline = gas_kohm;
  } else {
    const float decayed = b.baseline * (1.0f - GAS_BASELINE_DECAY);
    b.baseline = (gas_kohm > decayed) ? gas_kohm : decayed;
  }
  if (b.n <= GAS_BASELINE_WARMUP_SAMPLES) {
    return 1.0f;                               // 예열 구간: 기준값이 아직 불안정
  }
  return gas_kohm / b.baseline;
}
