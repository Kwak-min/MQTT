#pragma once
/*
 * firmware/include/qos_calibration.h
 * ─────────────────────────────────────────────────────────────────────────────
 * TinyML이 오프라인으로 학습한 QoS 임계값 "보정폭(delta)"을 담습니다.
 *
 * ★ 이 파일은 ml_model/train_threshold_calibration.py가 학습 결과로 덮어씁니다. 직접 수정하지 마세요. ★
 *
 * 설계 원칙 — "사람이 정한 기준이 항상 이긴다"
 *   사람이 명시한 기준(SPEC: main_gingerbread.cpp의 SystemConfig 기본값 30/50/70/85 —
 *   TEMP_WARN_C/TEMP_DANGER_C/HUM_WARN_PCT/HUM_DANGER_PCT)이 최종 결정권을 가집니다.
 *   TinyML은 그 기준값 자체를 대체하지 않고, 거기서 아주 조금만(최대 ±TEMP_ADJUST_LIMIT_C,
 *   ±HUM_ADJUST_LIMIT_PCT — main_gingerbread.cpp에 하드코딩) 조정할 수 있습니다.
 *   이 최대 폭은 학습 결과와 무관하게 펌웨어 쪽에서 항상 clamp되므로, 학습이 잘못되거나
 *   이 헤더 값이 손상되어도 "기준이 아예 사라지거나 크게 벗어나는" 일은 구조적으로 불가능합니다.
 *
 * 현재 상태: 학습되지 않은 기본값 (QOS_CALIBRATION_TRAINED = 0, delta 전부 0.0 → SPEC 그대로 사용)
 *   ml_model/README.md 절차로 데이터를 수집하고 ml_model/train_threshold_calibration.py를 실행하면
 *   실제 데이터로 학습된 값으로 교체됩니다. 부팅 시 시리얼에 학습 여부가 표시됩니다.
 * ─────────────────────────────────────────────────────────────────────────────
 */

// 1 = ml_model/train_threshold_calibration.py가 실제 데이터로 학습해 생성한 값, 0 = 학습 전 기본값(delta=0)
#define QOS_CALIBRATION_TRAINED 0

// SPEC(main_gingerbread.cpp SystemConfig 기본값) 대비 보정폭. 단위는 각각 °C, %.
// 양수면 임계값을 올리는(더 둔감해지는) 방향, 음수면 낮추는(더 민감해지는) 방향입니다.
static const float QOS_CAL_TEMP_WARN_DELTA_C    = 0.0f;
static const float QOS_CAL_TEMP_DANGER_DELTA_C  = 0.0f;
static const float QOS_CAL_HUM_WARN_DELTA_PCT   = 0.0f;
static const float QOS_CAL_HUM_DANGER_DELTA_PCT = 0.0f;
