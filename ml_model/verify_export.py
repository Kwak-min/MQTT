"""
ml_model/verify_export.py
─────────────────────────────────────────────────────────────────────────────
펌웨어가 실제로 쓰는 신경망 코드(firmware/include/mlp_inference.h + mlp_weights.h)를
PC의 g++로 컴파일해서, 파이썬 모델과 출력이 같은지 대조합니다.

  python ml_model/verify_export.py

확인하는 것
  1) C++ 순전파(float32)  vs  헤더에 적힌 값으로 만든 NumPy 순전파(float64)
       → mlp_inference.h의 계산 코드와 헤더 문법/값 파싱이 맞는지
  2) (ml_model/data/mlp_model.npz가 있으면) C++  vs  train.py가 학습한 float64 모델
       → 헤더로 내보내는 과정에서 값이 손상되지 않았는지 (반올림 외 오차 없음)
  3) 헤더의 QoS 임계값이 학습 결과와 같은지, MLP_WEIGHTS_TRAINED 값

오차가 5e-5를 넘으면 실패(종료 코드 1)합니다. 정상이면 float32 반올림 수준입니다
(기압처럼 값이 큰 입력이 있으면 1e-6대까지 나올 수 있고, 그보다 크면 헤더나 코드가 잘못된 것입니다).
이 검사는 업로드 전에 반드시 한 번 실행하세요. 보드 없이 PC에서 됩니다.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Dict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
# 점수(0~1) 오차 허용치. C++는 float32, 기준은 float64입니다. 기압(약 1000 hPa)처럼 값이 큰 입력은 float32
# 반올림이 표준화 후에 증폭되어 수 μ(1e-6) 오차가 생길 수 있어 여유를 둡니다. QoS 임계값 간격(0.005)에 비하면 무시할 수준입니다.
TOLERANCE = 5e-5

HARNESS = r"""
#include <stdio.h>
#include "mlp_inference.h"
int main() {
  float t, h, g, p, r;
  while (scanf("%f %f %f %f %f", &t, &h, &g, &p, &r) == 5) printf("%.9g\n", mlp_forward(t, h, g, p, r));
  return 0;
}
"""

# 가스 기준값 추적기(gas_baseline.h)를 값 하나씩 먹여 비율을 출력하는 하네스
BASELINE_HARNESS = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "gas_baseline.h"
int main() {
  GasBaseline b;
  gas_baseline_reset(b);
  char tok[64];
  // MinGW의 scanf("%f")는 "nan" 문자열을 읽지 못하고 입력을 멈추므로, 문자열로 받아 직접 변환합니다.
  while (scanf("%63s", tok) == 1) {
    const float v = (strcmp(tok, "nan") == 0) ? (float)NAN : (float)atof(tok);
    printf("%.9g\n", gas_baseline_update(b, v));
  }
  return 0;
}
"""


def parse_header(path: str) -> Dict[str, object]:
    """mlp_weights.h에서 상수를 읽습니다."""
    text = open(path, encoding="utf-8").read()
    num = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"

    def arr(name: str) -> np.ndarray:
        m = re.search(rf"{name}\s*(?:\[\s*\d+\s*\])+\s*=\s*\{{(.*?)\}}\s*;", text, re.S)
        if not m:
            raise SystemExit(f"헤더에서 {name} 배열을 찾지 못했습니다: {path}")
        return np.array([float(x) for x in re.findall(num, m.group(1))])

    def scalar(name: str) -> float:
        m = re.search(rf"{name}\s*=\s*({num})f?\s*;", text)
        if not m:
            raise SystemExit(f"헤더에서 {name} 값을 찾지 못했습니다: {path}")
        return float(m.group(1))

    trained = re.search(r"#define\s+MLP_WEIGHTS_TRAINED\s+(\d)", text)
    mean = arr("MLP_MEAN")
    if len(mean) != 5:
        raise SystemExit(f"헤더의 입력이 {len(mean)}개입니다. 이 검증기는 5입력(온도, 습도, 가스저항, 기압, 가스비율) 헤더용입니다.\n"
                         "  구버전 헤더라면 train.py로 다시 생성하세요.")
    return {
        "trained": int(trained.group(1)) if trained else None,
        "mean": mean, "std": arr("MLP_STD"),
        "W1": arr("MLP_W_HIDDEN").reshape(5, 5), "b1": arr("MLP_B_HIDDEN"),
        "W2": arr("MLP_W_OUTPUT"), "b2": scalar("MLP_B_OUTPUT"),
        "t1": scalar("MLP_TH_QOS1"), "t2": scalar("MLP_TH_QOS2"),
    }


def numpy_forward(p: Dict[str, object], X: np.ndarray) -> np.ndarray:
    Xn = (X - p["mean"]) / p["std"]
    H = np.maximum(Xn @ p["W1"].T + p["b1"], 0.0)
    return 1.0 / (1.0 + np.exp(-(H @ p["W2"] + p["b2"])))


def run_cpp(header_dir: str, X: np.ndarray) -> np.ndarray:
    cxx = shutil.which("g++") or shutil.which("c++")
    if not cxx:
        raise SystemExit("g++ 를 찾지 못했습니다. MinGW/GCC를 설치하거나 PATH에 추가하세요 (이 검증은 g++ 가 필요합니다).")
    with tempfile.TemporaryDirectory() as tmp:
        src, exe = os.path.join(tmp, "harness.cpp"), os.path.join(tmp, "harness.exe")
        open(src, "w").write(HARNESS)
        r = subprocess.run([cxx, "-O2", "-Wall", "-Wextra", "-I", header_dir, src, "-o", exe],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit("헤더/추론 코드가 컴파일되지 않습니다:\n" + r.stderr)
        if r.stderr.strip():
            print("컴파일 경고:\n" + r.stderr)
        inp = "\n".join(" ".join(f"{v:.6f}" for v in row) for row in X)
        out = subprocess.run([exe], input=inp, capture_output=True, text=True).stdout.split()
    if len(out) != len(X):
        raise SystemExit(f"C++ 출력 개수가 다릅니다 ({len(out)} != {len(X)})")
    return np.array([float(v) for v in out])


def verify_gas_baseline() -> bool:
    """
    firmware/include/gas_baseline.h(C++, g++로 컴파일)와 ml_model/gas_baseline.py(파이썬)가 같은 비율을 내는지
    여러 시나리오의 가스 시계열로 대조합니다. 학습에 쓴 가스비율이 실기에서 계산될 값과 같다는 보증입니다.
    """
    from gas_baseline import load_constants, ratio_sequence

    include_dir = os.path.join(ROOT, "firmware", "include")
    cxx = shutil.which("g++") or shutil.which("c++")
    if not cxx:
        raise SystemExit("g++ 를 찾지 못했습니다 (가스 기준값 검증에 필요).")
    warmup, decay = load_constants()
    rng = np.random.default_rng(1)
    n = 1500
    steady = 100.0 * np.exp(rng.normal(0, 0.05, n))                       # 잡음이 있는 정상 상태
    event = steady.copy()
    event[600:900] *= 0.3                                                 # 가스 발생 (기준의 30%)
    ramp = np.concatenate([np.linspace(20, 100, 200), 100 * np.exp(rng.normal(0, 0.03, n - 200))])  # 예열 상승
    bad = steady.copy()
    bad[[50, 51, 700]] = [0.0, -5.0, float("nan")]                        # 유효하지 않은 값
    wide = np.exp(rng.uniform(np.log(1.0), np.log(900.0), n))             # 넓은 범위의 무작위 값
    series = {"정상+잡음": steady, "가스 발생": event, "예열 상승": ramp, "무효값 포함": bad, "무작위 범위": wide}

    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        src, exe = os.path.join(tmp, "bl.cpp"), os.path.join(tmp, "bl.exe")
        open(src, "w").write(BASELINE_HARNESS)
        r = subprocess.run([cxx, "-O2", "-Wall", "-Wextra", "-I", include_dir, src, "-o", exe],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit("gas_baseline.h 가 컴파일되지 않습니다:\n" + r.stderr)
        print(f"  가스 기준값 추적기 (예열 {warmup}샘플, 감쇠 {float(decay):.6f}):")
        for name, vals in series.items():
            inp = "\n".join("nan" if np.isnan(v) else f"{v:.6f}" for v in vals)
            out = np.array([float(x) for x in subprocess.run([exe], input=inp, capture_output=True, text=True).stdout.split()])
            py, _ = ratio_sequence([float(f"{v:.6f}") if not np.isnan(v) else v for v in vals], warmup, decay)
            d = float(np.max(np.abs(out - py)))
            good = len(out) == len(py) and d < TOLERANCE
            ok = ok and good
            print(f"    - {name:<10} 최대 오차 {d:.2e}  → {'PASS' if good else 'FAIL'}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="펌웨어 MLP 코드(g++)와 파이썬 모델의 출력 대조")
    ap.add_argument("--header", default=os.path.join(ROOT, "firmware", "include", "mlp_weights.h"))
    ap.add_argument("--model", default=os.path.join(HERE, "data", "mlp_model.npz"))
    ap.add_argument("--n", type=int, default=5000)
    args = ap.parse_args()

    p = parse_header(args.header)
    header_dir = os.path.dirname(os.path.abspath(args.header))
    if not os.path.exists(os.path.join(header_dir, "mlp_inference.h")):
        raise SystemExit(f"mlp_inference.h 가 헤더와 같은 폴더에 있어야 합니다: {header_dir}")

    rng = np.random.default_rng(0)
    X = np.column_stack([rng.uniform(-20, 85, args.n), rng.uniform(0, 100, args.n),
                         np.exp(rng.uniform(np.log(1.0), np.log(500.0), args.n)),    # 가스: 로그 균등 1~500 kΩ
                         rng.uniform(300, 1100, args.n),                              # 기압: BME680 측정 범위 hPa
                         rng.uniform(0.02, 1.05, args.n)])                            # 가스비율: 0~1 (경계 밖 값도 포함)
    cpp = run_cpp(header_dir, X)

    d1 = float(np.max(np.abs(cpp - numpy_forward(p, X))))
    print(f"헤더: {os.path.relpath(args.header, ROOT)}   MLP_WEIGHTS_TRAINED = {p['trained']}"
          + ("   ⚠ 학습되지 않은 임시값" if p["trained"] == 0 else ""))
    print(f"입력 {args.n}개")
    print(f"  1) C++(float32) vs 헤더 값 기반 NumPy: 최대 오차 {d1:.2e}  → {'PASS' if d1 < TOLERANCE else 'FAIL'}")
    ok = d1 < TOLERANCE

    if os.path.exists(args.model):
        m = np.load(args.model)
        ref = {"mean": m["mean"], "std": m["std"], "W1": m["W1"], "b1": m["b1"], "W2": m["W2"], "b2": float(m["b2"])}
        d2 = float(np.max(np.abs(cpp - numpy_forward(ref, X))))
        th = abs(p["t1"] - float(m["t1"])) < 1e-6 and abs(p["t2"] - float(m["t2"])) < 1e-6
        print(f"  2) C++ vs train.py 학습 모델(float64): 최대 오차 {d2:.2e}  → {'PASS' if d2 < TOLERANCE else 'FAIL'}")
        print(f"  3) 헤더 임계값 == 학습 결과 ({p['t1']:.4f}, {p['t2']:.4f}): {'PASS' if th else 'FAIL'}")
        ok = ok and d2 < TOLERANCE and th
    else:
        print(f"  (참고) {os.path.relpath(args.model, ROOT)} 가 없어 학습 모델과의 대조(2, 3)는 생략했습니다.")

    print("  4) 가스 기준값 추적기: 펌웨어(C++) vs 학습에 쓴 파이썬 재현")
    ok = verify_gas_baseline() and ok

    print("결과:", "모두 통과" if ok else "실패 — 업로드하지 마세요")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
