#pragma once
/*
 * firmware/include/mlp_inference.h
 * ─────────────────────────────────────────────────────────────────────────────
 * 위험 점수 MLP(5-5-1) 순전파. 표준 C++만 사용하므로 PC에서도 그대로 컴파일됩니다.
 * (ml_model/verify_export.py가 이 파일을 g++로 컴파일해 파이썬 모델과 출력을 대조합니다.)
 *
 *   x     = (입력 - MLP_MEAN) / MLP_STD                       표준화
 *   h[i]  = ReLU( Σ_j x[j] * W_HIDDEN[i][j] + B_HIDDEN[i] )   은닉층 5 노드
 *   score = Sigmoid( Σ_i h[i] * W_OUTPUT[i] + B_OUTPUT )      출력 1 노드, 0.0 ~ 1.0
 *
 * 가중치와 상수는 mlp_weights.h에서 가져옵니다.
 * ─────────────────────────────────────────────────────────────────────────────
 */

#include <math.h>
#include "mlp_weights.h"

static inline float mlp_forward(float temp, float hum, float gas_kohm, float pres_hpa, float gas_ratio) {
  const float raw[5] = { temp, hum, gas_kohm, pres_hpa, gas_ratio };
  float x[5];
  for (int j = 0; j < 5; j++) {
    x[j] = (raw[j] - MLP_MEAN[j]) / MLP_STD[j];
  }

  float z_out = MLP_B_OUTPUT;
  for (int i = 0; i < 5; i++) {
    float z = MLP_B_HIDDEN[i];
    for (int j = 0; j < 5; j++) {
      z += x[j] * MLP_W_HIDDEN[i][j];
    }
    const float h = (z > 0.0f) ? z : 0.0f;   // ReLU
    z_out += h * MLP_W_OUTPUT[i];
  }
  return 1.0f / (1.0f + expf(-z_out));       // Sigmoid
}
