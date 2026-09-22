#pragma once
/*
 * firmware/include/mlp_weights.h
 * ─────────────────────────────────────────────────────────────────────────────
 * 위험 점수 MLP(5-5-1)의 가중치, 입력 정규화 상수, QoS 임계값.
 *
 * ★ 이 파일은 ml_model/train.py가 학습 결과로 덮어씁니다. 직접 수정하지 마세요. ★
 *
 * 현재 상태: 학습되지 않은 임시값 (MLP_WEIGHTS_TRAINED = 0)
 *   아래 값은 실제 데이터로 학습한 것이 아니라 다른 AI가 만들어 준 숫자입니다.
 *   학습 데이터셋, 학습 스크립트, 검증 기록이 존재하지 않습니다.
 *   이 값을 "학습된 모델"이라고 서술하면 안 됩니다. 부팅 시 시리얼에 경고가 출력됩니다.
 *   → ml_model/README.md의 절차로 데이터를 수집하고 ml_model/train.py --features temp,hum 을 실행하세요.
 *
 * [2026-09] QoS 판단 기준을 온도·습도로만 제한하기로 하여, 가스저항/가스비율 입력 열의
 *   가중치도 0으로 고정했습니다(기압은 이미 0이었음). 학습 시에도 --features temp,hum 을
 *   써야 이 제약이 유지됩니다. main_gingerbread.cpp가 MLP_WEIGHTS_TRAINED를 보고 QoS를
 *   직접 결정할지(1), 사람이 정한 SPEC 규칙으로 폴백할지(0) 정합니다.
 * ─────────────────────────────────────────────────────────────────────────────
 */

// 1 = ml_model/train.py가 실제 데이터로 학습해 생성한 값, 0 = 학습되지 않은 임시값
#define MLP_WEIGHTS_TRAINED 0

// 입력 표준화: x_norm = (x_raw - MEAN) / STD
// 입력 순서: 온도[°C], 습도[%], 가스저항[kΩ], 기압[hPa], 가스비율(현재/기준값, 0~1)
//   앞의 4개는 BME680 측정값이고, 가스비율은 gas_baseline.h가 계산하는 파생값입니다.
// QoS 판단은 온도·습도로만 제한하므로, 가스저항/기압/가스비율 세 입력의 가중치는 모두 0입니다.
static const float MLP_MEAN[5] = { 25.0f, 50.0f, 30.0f, 1013.25f, 1.0f };
static const float MLP_STD[5]  = { 10.0f, 20.0f, 15.0f, 10.0f, 0.2f };

// 은닉층(5 노드): h[i] = ReLU( Σ_j x[j] * MLP_W_HIDDEN[i][j] + MLP_B_HIDDEN[i] )
static const float MLP_W_HIDDEN[5][5] = {
  {  0.45f, -0.21f, 0.0f, 0.0f, 0.0f },
  { -0.12f,  0.34f, 0.0f, 0.0f, 0.0f },
  {  0.67f,  0.11f, 0.0f, 0.0f, 0.0f },
  { -0.29f, -0.55f, 0.0f, 0.0f, 0.0f },
  {  0.51f,  0.22f, 0.0f, 0.0f, 0.0f },
};
static const float MLP_B_HIDDEN[5] = { 0.12f, -0.05f, 0.23f, -0.18f, 0.08f };

// 출력층(1 노드): score = Sigmoid( Σ_i h[i] * MLP_W_OUTPUT[i] + MLP_B_OUTPUT )
static const float MLP_W_OUTPUT[5] = { 0.88f, 0.65f, -0.24f, 0.95f, 0.41f };
static const float MLP_B_OUTPUT    = -0.32f;

// 점수 → QoS 매핑:  score >= MLP_TH_QOS2 → QoS 2,  score >= MLP_TH_QOS1 → QoS 1,  그 외 QoS 0
static const float MLP_TH_QOS1 = 0.40f;
static const float MLP_TH_QOS2 = 0.75f;
