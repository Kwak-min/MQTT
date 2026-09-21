"""
ml_model/gas_baseline.py
─────────────────────────────────────────────────────────────────────────────
firmware/include/gas_baseline.h (가스 저항의 기준값 대비 비율 추적기)를 파이썬으로 똑같이 재현합니다.

학습 데이터의 gas_kohm 시계열에서 펌웨어가 실시간으로 계산할 "가스 비율"을 만들어 학습에 쓰기 위한 것입니다.
학습과 배포가 다르게 계산하면 학습한 임계값이 실기에서 어긋나므로 다음 두 가지로 일치를 보장합니다.
  1) 상수(WARMUP, DECAY)는 이 파일에 적지 않고 펌웨어 헤더에서 읽습니다 (한 곳에서만 정의).
  2) verify_export.py가 펌웨어 헤더를 g++로 컴파일해 이 구현과 출력을 대조합니다.

계산은 C++와 같은 float32로 합니다.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import os
import re
from typing import Tuple

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HEADER = os.path.join(ROOT, "firmware", "include", "gas_baseline.h")

# 같은 세션 안에서 연속한 두 샘플의 간격이 이보다 크면(수집 주기 5초의 6배) 수집이 끊긴 것으로 보고
# 기준값을 새로 시작합니다. 보드가 재부팅되어 ms가 줄어드는 경우도 새로 시작합니다.
# (실제 배포에서는 재부팅 때만 초기화되므로, 수집이 끊긴 구간은 근사입니다.)
GAP_MS = 30_000


def load_constants(path: str = HEADER) -> Tuple[int, np.float32]:
    """펌웨어 헤더에서 (예열 샘플 수, 샘플당 감쇠율)을 읽습니다."""
    text = open(path, encoding="utf-8").read()
    w = re.search(r"#define\s+GAS_BASELINE_WARMUP_SAMPLES\s+(\d+)", text)
    d = re.search(r"#define\s+GAS_BASELINE_DECAY\s+([-+0-9.eE]+)f?", text)
    if not w or not d:
        raise SystemExit(f"{path} 에서 GAS_BASELINE_WARMUP_SAMPLES / GAS_BASELINE_DECAY 를 찾지 못했습니다.")
    return int(w.group(1)), np.float32(float(d.group(1)))


def ratio_sequence(values, warmup: int, decay: np.float32) -> Tuple[np.ndarray, np.ndarray]:
    """
    연속된 가스 저항(kΩ) 시계열을 펌웨어와 똑같이 처리합니다.
    반환: (비율 배열, 각 시점의 유효 샘플 수 n). n <= warmup 이면 예열 구간(비율은 1.0 고정).
    """
    one = np.float32(1.0)
    baseline, n = np.float32(0.0), 0
    ratios = np.empty(len(values), dtype=float)
    counts = np.zeros(len(values), dtype=int)
    for i, raw in enumerate(values):
        v = np.float32(raw)
        if not (v > 0.0):                       # 유효하지 않은 값(0 이하, NaN): 상태 유지, 중립값
            ratios[i], counts[i] = 1.0, n
            continue
        n += 1
        if n == 1:
            baseline = v
        else:
            decayed = np.float32(baseline * (one - decay))
            baseline = v if v > decayed else decayed
        ratios[i] = 1.0 if n <= warmup else float(np.float32(v / baseline))
        counts[i] = n
    return ratios, counts


def add_gas_ratio(df: pd.DataFrame, gap_ms: int = GAP_MS,
                  path: str = HEADER) -> Tuple[pd.DataFrame, int, int]:
    """
    데이터프레임에 gas_ratio(가스 비율)와 gas_n(구간 내 유효 샘플 수) 컬럼을 추가합니다.
    행은 파일 순서(= 수집 시간순)라고 가정합니다. 세션 안에서 수집이 끊기거나(gap_ms 초과) ms가 줄면
    새 구간으로 나누어 기준값을 새로 시작합니다.
    반환: (df, 예열 샘플 수, 구간 수)
    """
    warmup, decay = load_constants(path)
    df = df.copy()
    df["gas_ratio"] = 1.0
    df["gas_n"] = 0
    segments = 0
    for _, idx in df.groupby("session", sort=False).indices.items():
        idx = list(idx)                                   # 파일 순서 유지
        ms = df["ms"].to_numpy()[idx]
        gas = df["gas_kohm"].to_numpy()[idx]
        start = 0
        for k in range(1, len(idx) + 1):
            if k == len(idx) or ms[k] < ms[k - 1] or ms[k] - ms[k - 1] > gap_ms:
                r, n = ratio_sequence(gas[start:k], warmup, decay)
                df.iloc[idx[start:k], df.columns.get_loc("gas_ratio")] = r
                df.iloc[idx[start:k], df.columns.get_loc("gas_n")] = n
                segments += 1
                start = k
    return df, warmup, segments
