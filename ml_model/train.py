"""
ml_model/train.py
─────────────────────────────────────────────────────────────────────────────
수집한 데이터(ml_model/data/raw_dataset.csv)로 위험 점수 MLP(5-5-1)를 학습하고,
펌웨어용 가중치 헤더(firmware/include/mlp_weights.h)로 내보냅니다.

  python ml_model/train.py                              # 권장 특징(온도, 습도, 가스비율)으로 학습
  python ml_model/train.py --compare-features           # 특징 조합별 성능을 비교만 하고 종료
  python ml_model/train.py --features temp,gasr         # 고른 특징만 사용
  python ml_model/train.py --val-sessions 20260922-1030 # 이 세션 전체를 검증용으로 (새 세션 일반화 확인)

입력 특징 (펌웨어 입력 순서와 같음)
  temp  온도 °C         hum   습도 %          gas   가스저항 kΩ (절대값)
  pres  기압 hPa        gasr  가스비율 = 가스저항 / 기준값 (0~1, firmware/include/gas_baseline.h)
  앞의 4개가 BME680이 측정하는 값 전부이고, gasr 은 가스저항에서 파생한 값입니다.
  --features 로 쓸 것을 고르며, 쓰지 않는 특징의 입력 가중치는 0으로 고정됩니다.

  기본값이 temp,hum,gasr 인 이유 (합성 데이터 실험, README 참조)
    · 가스저항 절대값(gas)은 센서 개체·예열·환경에 따라 정상 기준이 세션마다 크게 달라 새 세션에서 무너집니다.
      기준값 대비 비율(gasr)은 이 차이를 상쇄합니다.
    · 기압(pres)은 위험의 직접 지표가 아니며 세션을 구별하는 "지문"이 되어 검증 성능을 부풀릴 수 있습니다.
    필요하면 --features 로 넣을 수 있고, --compare-features 로 실제로 도움이 되는지 확인할 수 있습니다.

모델 (펌웨어 mlp_inference.h와 동일한 구조)
  x = (입력 - mean) / std  →  h = ReLU(W1 x + b1)  [5 노드]  →  score = Sigmoid(W2 h + b2)
  라벨 0(정상)/1(경고)/2(위험)를 목표 점수 0.0/0.5/1.0으로 두고 학습합니다 (순서형 소프트 타깃, BCE).
  점수 → QoS: score >= TH_QOS2 → QoS 2, score >= TH_QOS1 → QoS 1, 그 외 QoS 0.
  두 임계값은 하드코딩하지 않고 "학습 데이터"에서 macro-F1이 최대가 되도록 정해 헤더로 내보냅니다.

가스비율 계산 (ml_model/gas_baseline.py)
  펌웨어가 실시간으로 계산할 값을 CSV의 gas_kohm 시계열에서 똑같이 재현합니다. 그래서 라벨이 없는
  샘플(label = -1)도 시계열 연속성을 위해 CSV에 있어야 하며, 여기서는 비율을 계산한 뒤 학습에서 제외합니다.
  부팅 후 예열 구간(GAS_BASELINE_WARMUP_SAMPLES)의 샘플은 비율이 1.0으로 고정되어 정보가 없으므로 학습에서 뺍니다.

여러 센서값을 쓸 때의 함정 (README 참조)
  • 열풍기로 가열하면 상대습도가 함께 떨어지고, 가스저항도 온도·습도의 영향을 받습니다. 조건을 바꿔 가며
    (가열만, 가스만, 습도만, 둘 다) 수집해 서로의 상관을 끊어 주세요.
  • 세션마다 정상 가스저항 중앙값이 얼마나 다른지 이 스크립트가 보여 주고, 크게 다르면 경고합니다.

정직한 평가를 위한 장치
  • 분할: 세션·라벨별로 시간순 뒤쪽 20%를 검증용으로 떼어 둡니다 (무작위 분할은 이웃 샘플이 비슷해 성능이
         부풀려집니다). --val-sessions 로 세션 전체를 검증용으로 쓰면 더 엄격합니다.
  • 임계값은 학습 데이터에서 정하고, 검증 데이터로만 성능을 보고합니다.
  • 기준선: 특징 하나만 쓰는 임계값 규칙 2개 중 학습 데이터에서 가장 좋은 것과 같은 방식으로 비교합니다
    ("높을수록/낮을수록 위험"인 방향도 자동 판정). MLP가 이 단순 규칙보다 나은지 확인하세요.
  • 클래스별 표본이 너무 적거나 라벨이 빠져 있으면 헤더를 내보내지 않습니다 (--force로 우회 가능).
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from gas_baseline import add_gas_ratio, load_constants  # noqa: E402

FEATURE_ORDER = ["temp", "hum", "gas", "pres", "gasr"]     # 펌웨어 입력 순서 (mlp_forward 인자 순서)
FEATURE_COLUMN = {"temp": "temp", "hum": "hum", "gas": "gas_kohm", "pres": "pres_hpa", "gasr": "gas_ratio"}
FEATURE_NAME = {"temp": "온도", "hum": "습도", "gas": "가스저항", "pres": "기압", "gasr": "가스비율"}
FEATURE_UNIT = {"temp": "°C", "hum": "%", "gas": "kΩ", "pres": "hPa", "gasr": ""}
DEFAULT_FEATURES = "temp,hum,gasr"
N_IN = len(FEATURE_ORDER)
N_HIDDEN = 5                                              # 펌웨어의 은닉 노드 수 (mlp_inference.h와 같아야 함)
SOFT_TARGET = np.array([0.0, 0.5, 1.0])                   # 라벨 0/1/2 → 목표 점수
CLASS_NAMES = ["정상(QoS 0)", "경고(QoS 1)", "위험(QoS 2)"]
GAS_DRIFT_WARN = 0.30                                     # 세션 간 정상 가스저항 중앙값 편차가 이 비율을 넘으면 경고


# ──────────────────────────────────────────────────────────────────────────────
# 데이터
# ──────────────────────────────────────────────────────────────────────────────

def load_dataset(path: str, feats: List[str]) -> Tuple[pd.DataFrame, dict]:
    """
    CSV를 읽고 학습용 데이터프레임을 만듭니다.
      1) 가스비율은 라벨 없는 행(-1)까지 포함한 전체 시계열로 먼저 계산합니다 (펌웨어가 실시간으로 보는 값과 같게).
      2) 그다음 라벨이 0/1/2인 행만 남기고, 가스비율을 쓰면 예열 구간 행을 뺍니다.
    반환: (데이터프레임, 정보 딕셔너리)
    """
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        raise SystemExit(f"데이터가 없습니다: {path}\n  → ml_model/README.md의 수집 절차(collect.py)를 먼저 진행하세요.")
    df = pd.read_csv(path)
    base = {"session", "ms", "temp", "hum", "gas_kohm", "label"}
    if not base <= set(df.columns):
        raise SystemExit(f"CSV에 필요한 컬럼이 없습니다: {sorted(base - set(df.columns))}")
    if "pres_hpa" not in df.columns:
        if "pres" in feats:
            raise SystemExit("CSV에 기압(pres_hpa) 컬럼이 없습니다 (기압이 없는 구버전 수집 데이터).\n"
                             f"  → --features {DEFAULT_FEATURES} 처럼 기압을 빼고 실행하세요.")
        df["pres_hpa"] = np.nan
    n0 = len(df)
    for c in ("ms", "temp", "hum", "gas_kohm", "pres_hpa", "label"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["ms", "label"]).reset_index(drop=True)
    df["session"] = df["session"].astype(str)

    df, warmup, segments = add_gas_ratio(df)                  # 라벨 없는 행 포함, 파일 순서대로
    n_unlabeled = int((df["label"] == -1).sum())
    df = df[df["label"].isin([0, 1, 2])].copy()
    df["label"] = df["label"].astype(int)

    need = [FEATURE_COLUMN[f] for f in feats if f != "gasr"]  # 쓰는 특징만 결측을 허용하지 않음
    df = df.dropna(subset=need)
    warmup_dropped = 0
    if "gasr" in feats:
        before = len(df)
        df = df[df["gas_n"] > warmup]                          # 예열 구간은 비율이 1.0 고정이라 정보가 없음
        warmup_dropped = before - len(df)
    for f in FEATURE_ORDER:                                    # 안 쓰는 특징은 0으로 (가중치가 0이라 무관)
        df[FEATURE_COLUMN[f]] = df[FEATURE_COLUMN[f]].fillna(0.0)
    info = {"raw_rows": n0, "unlabeled_rows": n_unlabeled, "warmup_samples": warmup,
            "warmup_rows_dropped": warmup_dropped, "gas_segments": segments,
            "invalid_rows_dropped": n0 - n_unlabeled - len(df) - warmup_dropped}
    return df.reset_index(drop=True), info


def split_blocks(df: pd.DataFrame, val_frac: float,
                 val_sessions: Optional[List[str]] = None) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    검증 데이터를 분리합니다.
      • val_sessions 가 있으면 그 세션 전체를 검증용으로 (새 세션에서의 일반화를 확인, 가장 엄격)
      • 없으면 세션·라벨 구간별로 시간순 뒤쪽 val_frac를 검증용으로 (구간이 10개 미만이면 전부 학습용)
    """
    if val_sessions:
        known = set(df["session"].unique())
        unknown = [s for s in val_sessions if s not in known]
        if unknown:
            raise SystemExit(f"--val-sessions 에 없는 세션: {unknown}\n  데이터에 있는 세션: {sorted(known)}")
        is_val = df["session"].isin(val_sessions)
        train_df, val_df = df[~is_val], df[is_val]
        if len(train_df) == 0:
            raise SystemExit("모든 세션을 검증용으로 지정해 학습 데이터가 없습니다.")
        return train_df, val_df
    train_parts, val_parts = [], []
    for _, g in df.groupby(["session", "label"], sort=False):
        g = g.sort_values("ms")
        k = int(round(len(g) * val_frac)) if len(g) >= 10 else 0
        if len(g) >= 10:
            k = max(k, 1)
        train_parts.append(g.iloc[: len(g) - k])
        val_parts.append(g.iloc[len(g) - k:])
    return pd.concat(train_parts), pd.concat(val_parts)


def class_weights(y: np.ndarray) -> np.ndarray:
    """표본별 가중치: 존재하는 각 클래스가 손실에 똑같이 기여하도록 (정상 데이터가 압도적으로 많아도 무관)."""
    present = [c for c in (0, 1, 2) if (y == c).any()]
    w = np.zeros(len(y))
    for c in present:
        w[y == c] = len(y) / (len(present) * (y == c).sum())
    return w


def session_drift_report(df: pd.DataFrame, feats: List[str]) -> Optional[dict]:
    """세션별 '정상' 데이터의 특징 중앙값을 보여 주고, 가스저항 기준값이 세션마다 크게 다르면 경고합니다."""
    normal = df[df["label"] == 0]
    if normal["session"].nunique() < 2:
        return None
    cols = [FEATURE_COLUMN[f] for f in feats]
    med = normal.groupby("session")[cols].median()
    print("\n세션별 '정상' 데이터 중앙값 (세션 간 차이가 크면 모델이 세션 간에 일반화되지 않습니다):")
    print("  " + med.round(3).to_string().replace("\n", "\n  "))
    out = {}
    if "gas" in feats:
        g = med["gas_kohm"]
        spread = float((g.max() - g.min()) / max(g.median(), 1e-9))
        out["gas_normal_median_spread"] = round(spread, 3)
        if spread > GAS_DRIFT_WARN:
            print(f"  ⚠ 가스저항 정상 중앙값이 세션마다 {spread * 100:.0f}% 차이납니다 (기준 {GAS_DRIFT_WARN * 100:.0f}%). "
                  "BME680 예열/개체/환경 영향일 수 있습니다.\n"
                  "     절대값(kΩ)으로 위험을 판단하면 다른 환경·시간에서 오작동합니다. 가스비율(gasr)을 쓰거나,\n"
                  "     충분히 예열한 뒤 수집하고 --val-sessions 로 새 세션 성능을 꼭 확인하세요.")
    if "gasr" in feats:
        r = med["gas_ratio"]
        out["gasr_normal_median_range"] = [round(float(r.min()), 3), round(float(r.max()), 3)]
    return out


# ──────────────────────────────────────────────────────────────────────────────
# 모델 (NumPy 구현 — 펌웨어와 같은 5-5-1 구조)
# ──────────────────────────────────────────────────────────────────────────────

def sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -60, 60)))


class MLP:
    def __init__(self, mask: np.ndarray, seed: int) -> None:
        rng = np.random.default_rng(seed)
        self.mask = mask.astype(float)                                 # 쓰지 않는 특징은 0
        self.W1 = rng.normal(0.0, 0.5, (N_HIDDEN, N_IN)) * self.mask   # (은닉, 입력)
        self.b1 = np.full(N_HIDDEN, 0.1)                               # 양수 편향: 죽은 ReLU 방지
        self.W2 = rng.normal(0.0, 0.5, N_HIDDEN)
        self.b2 = 0.0

    def params(self) -> Dict[str, np.ndarray]:
        return {"W1": self.W1, "b1": self.b1, "W2": self.W2, "b2": np.array(self.b2)}

    def forward(self, X: np.ndarray):
        Z1 = X @ self.W1.T + self.b1
        H = np.maximum(Z1, 0.0)
        return Z1, H, sigmoid(H @ self.W2 + self.b2)

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.forward(X)[2]

    def loss(self, X: np.ndarray, t: np.ndarray, w: np.ndarray) -> float:
        p = np.clip(self.predict(X), 1e-7, 1 - 1e-7)
        return float(np.sum(w * -(t * np.log(p) + (1 - t) * np.log(1 - p))) / np.sum(w))

    def grads(self, X, t, w, l2):
        Z1, H, p = self.forward(X)
        dz = w * (p - t) / np.sum(w)                       # BCE + 시그모이드의 기울기
        dW2 = H.T @ dz + 2 * l2 * self.W2
        db2 = float(np.sum(dz))
        dZ1 = (dz[:, None] * self.W2[None, :]) * (Z1 > 0)
        dW1 = (dZ1.T @ X + 2 * l2 * self.W1) * self.mask   # 쓰지 않는 특징의 기울기는 0
        db1 = dZ1.sum(axis=0)
        return {"W1": dW1, "b1": db1, "W2": dW2, "b2": np.array(db2)}


def train_one(model: MLP, Xtr, ttr, wtr, Xva, tva, wva, epochs: int, lr: float, l2: float,
              patience: int) -> Tuple[float, int]:
    """Adam으로 학습하고 검증 손실이 최저인 시점의 가중치로 되돌립니다. 반환: (최저 손실, 에폭)."""
    m = {k: np.zeros_like(v, dtype=float) for k, v in model.params().items()}
    v = {k: np.zeros_like(x, dtype=float) for k, x in model.params().items()}
    b1c, b2c, eps = 0.9, 0.999, 1e-8
    have_val = Xva is not None and len(Xva) > 0
    best = (np.inf, 0, None)
    for ep in range(1, epochs + 1):
        g = model.grads(Xtr, ttr, wtr, l2)
        for k in m:
            m[k] = b1c * m[k] + (1 - b1c) * g[k]
            v[k] = b2c * v[k] + (1 - b2c) * g[k] ** 2
            step = lr * (m[k] / (1 - b1c ** ep)) / (np.sqrt(v[k] / (1 - b2c ** ep)) + eps)
            if k == "W1":
                model.W1 -= step * model.mask
            elif k == "b1":
                model.b1 -= step
            elif k == "W2":
                model.W2 -= step
            else:
                model.b2 -= float(step)
        cur = model.loss(Xva, tva, wva) if have_val else model.loss(Xtr, ttr, wtr)
        if cur < best[0] - 1e-9:
            best = (cur, ep, (model.W1.copy(), model.b1.copy(), model.W2.copy(), float(model.b2)))
        elif ep - best[1] > patience:
            break
    model.W1, model.b1, model.W2, model.b2 = best[2]
    return best[0], best[1]


# ──────────────────────────────────────────────────────────────────────────────
# 평가 / 임계값
# ──────────────────────────────────────────────────────────────────────────────

def to_qos(values: np.ndarray, t1: float, t2: float) -> np.ndarray:
    return np.where(values >= t2, 2, np.where(values >= t1, 1, 0))


def macro_f1(y: np.ndarray, pred: np.ndarray) -> float:
    f1s = []
    for c in (0, 1, 2):
        if not (y == c).any():
            continue
        tp = float(((pred == c) & (y == c)).sum())
        fp = float(((pred == c) & (y != c)).sum())
        fn = float(((pred != c) & (y == c)).sum())
        f1s.append(0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn))
    return float(np.mean(f1s)) if f1s else 0.0


def _macro_f1_from_conf(conf: np.ndarray, present: np.ndarray) -> np.ndarray:
    """conf: (K, 3, 3) 혼동행렬 묶음 (행=실제, 열=예측) → 각각의 macro-F1 (K,)."""
    f1s = []
    for c in range(3):
        if not present[c]:
            continue
        tp = conf[:, c, c]
        fp = conf[:, :, c].sum(axis=1) - tp
        fn = conf[:, c, :].sum(axis=1) - tp
        denom = 2 * tp + fp + fn
        f1s.append(np.where(tp == 0, 0.0, 2 * tp / np.maximum(denom, 1e-12)))
    return np.mean(f1s, axis=0) if f1s else np.zeros(len(conf))


def confusion(y: np.ndarray, pred: np.ndarray) -> List[List[int]]:
    return [[int(((y == a) & (pred == b)).sum()) for b in (0, 1, 2)] for a in (0, 1, 2)]


def pick_thresholds(values: np.ndarray, y: np.ndarray, candidates: np.ndarray) -> Tuple[float, float, float]:
    """
    macro-F1이 최대인 (t1 < t2)를 찾습니다. 동점이면 최적 구간의 평균값을 써서 경계에 붙지 않게 합니다.
    클래스별 누적 개수(정렬 + searchsorted)로 모든 후보 쌍을 벡터화해서 평가하므로 후보가 수백 개여도 빠릅니다.
    """
    cands = np.asarray(candidates, dtype=float)
    n_c = np.array([(y == c).sum() for c in range(3)])
    present = n_c > 0
    cnt = np.stack([np.searchsorted(np.sort(values[y == c]), cands, side="left") for c in range(3)])  # (3, K): 후보 미만 개수
    best, pairs = -1.0, []
    for i in range(len(cands) - 1):
        js = np.arange(i + 1, len(cands))
        conf = np.empty((len(js), 3, 3))
        for c in range(3):
            conf[:, c, 0] = cnt[c, i]                         # 실제 c → 예측 0 (값 < t1)
            conf[:, c, 1] = cnt[c, js] - cnt[c, i]            # 예측 1 (t1 <= 값 < t2)
            conf[:, c, 2] = n_c[c] - cnt[c, js]               # 예측 2 (값 >= t2)
        f = _macro_f1_from_conf(conf, present)
        top = float(f.max())
        if top > best + 1e-12:
            best, pairs = top, [(cands[i], cands[js[k]]) for k in np.where(f >= top - 1e-12)[0]]
        elif abs(top - best) <= 1e-12:
            pairs += [(cands[i], cands[js[k]]) for k in np.where(f >= top - 1e-12)[0]]
    t1 = float(np.mean([p[0] for p in pairs]))
    t2 = float(np.mean([p[1] for p in pairs]))
    if macro_f1(y, to_qos(values, t1, t2)) < best - 1e-9:      # 최적 구간이 비연속이면 첫 최적쌍을 사용
        t1, t2 = float(pairs[0][0]), float(pairs[0][1])
    return t1, t2, best


def summarize(y: np.ndarray, pred: np.ndarray) -> dict:
    recall = {}
    for c in (0, 1, 2):
        n = int((y == c).sum())
        recall[str(c)] = round(float(((pred == c) & (y == c)).sum()) / n, 4) if n else None
    return {"n": int(len(y)), "accuracy": round(float((pred == y).mean()), 4),
            "macro_f1": round(macro_f1(y, pred), 4), "recall": recall, "confusion": confusion(y, pred)}


# ──────────────────────────────────────────────────────────────────────────────
# 학습 + 평가 한 번 (메인/특징 비교에서 공통 사용)
# ──────────────────────────────────────────────────────────────────────────────

def fit_and_eval(train_df: pd.DataFrame, val_df: pd.DataFrame, feats: List[str], args,
                 restarts: int, verbose: bool) -> dict:
    mask = np.array([1.0 if f in feats else 0.0 for f in FEATURE_ORDER])
    cols = [FEATURE_COLUMN[f] for f in FEATURE_ORDER]
    Xtr_raw, ytr = train_df[cols].to_numpy(float), train_df["label"].to_numpy(int)
    Xva_raw, yva = val_df[cols].to_numpy(float), val_df["label"].to_numpy(int)
    have_val = len(val_df) > 0 and len(set(yva)) == 3

    # 표준화: 사용하는 특징만 학습 데이터의 평균/표준편차를 씁니다 (안 쓰는 특징은 0/1로 고정)
    mean = np.where(mask > 0, Xtr_raw.mean(axis=0), 0.0)
    std = np.where(mask > 0, Xtr_raw.std(axis=0), 1.0)
    std = np.where(std < 1e-6, 1.0, std)
    Xtr, Xva = (Xtr_raw - mean) / std, (Xva_raw - mean) / std
    ttr, tva = SOFT_TARGET[ytr], SOFT_TARGET[yva]
    wtr = class_weights(ytr)
    wva = class_weights(yva) if len(yva) else np.array([])

    best_model, best_loss, log = None, np.inf, []
    for r in range(restarts):
        model = MLP(mask, seed=args.seed + r)
        loss, ep = train_one(model, Xtr, ttr, wtr, Xva if have_val else None, tva, wva,
                             args.epochs, args.lr, args.l2, args.patience)
        log.append({"seed": args.seed + r, "loss": round(loss, 5), "epoch": ep})
        if verbose:
            print(f"  시도 {r + 1}/{restarts}: {'검증' if have_val else '학습'} 손실 {loss:.4f} (에폭 {ep})")
        if loss < best_loss:
            best_model, best_loss = model, loss
    model = best_model

    # 임계값은 학습 데이터에서만 정하고, 성능은 검증 데이터로 보고합니다.
    s_tr = model.predict(Xtr)
    s_va = model.predict(Xva) if len(Xva) else np.array([])
    t1, t2, _ = pick_thresholds(s_tr, ytr, np.arange(0.02, 0.99, 0.005))
    return {"model": model, "mean": mean, "std": std, "t1": t1, "t2": t2, "have_val": have_val, "log": log,
            "train": summarize(ytr, to_qos(s_tr, t1, t2)),
            "val": summarize(yva, to_qos(s_va, t1, t2)) if len(yva) else None}


def single_feature_rules(train_df: pd.DataFrame, val_df: pd.DataFrame, feats: List[str]) -> List[dict]:
    """
    특징 하나만 쓰는 임계값 규칙(예: 가스비율 <= A → 위험, <= B → 경고)의 성능.
    임계값은 학습 데이터에서 고르고, 방향(높을수록/낮을수록 위험)은 라벨과의 상관 부호로 정합니다.
    후보 임계값을 MLP 쪽(약 190개)보다 촘촘하게(최대 400개) 잡아 기준선이 불리해지지 않게 합니다.
    """
    out = []
    ytr, yva = train_df["label"].to_numpy(int), val_df["label"].to_numpy(int)
    for f in feats:
        col = FEATURE_COLUMN[f]
        vtr = train_df[col].to_numpy(float)
        sign = 1.0
        if vtr.std() > 0 and ytr.std() > 0:
            sign = 1.0 if np.corrcoef(vtr, ytr)[0, 1] >= 0 else -1.0
        cand = np.unique(np.quantile(sign * vtr, np.linspace(0, 1, 401)))
        t1, t2, f1_tr = pick_thresholds(sign * vtr, ytr, cand)
        val = summarize(yva, to_qos(sign * val_df[col].to_numpy(float), t1, t2)) if len(yva) else None
        op = ">=" if sign > 0 else "<="
        out.append({"feature": f, "direction": "높을수록 위험" if sign > 0 else "낮을수록 위험",
                    "warn_at": sign * t1, "danger_at": sign * t2, "op": op,
                    "text": f"{FEATURE_NAME[f]} {op} {sign * t2:.3f}{FEATURE_UNIT[f]} → 위험, "
                            f"{op} {sign * t1:.3f}{FEATURE_UNIT[f]} → 경고",
                    "train_macro_f1": round(f1_tr, 4), "val": val})
    return out


# ──────────────────────────────────────────────────────────────────────────────
# 내보내기
# ──────────────────────────────────────────────────────────────────────────────

def cf(v: float) -> str:
    """float32로 정확히 왕복되는 C 리터럴 (예: 0.5f, -1.2e-05f). '0f' 같은 잘못된 형태를 만들지 않습니다."""
    s = f"{np.float32(v):.9g}"
    if "." not in s and "e" not in s and "inf" not in s and "nan" not in s:
        s += ".0"
    return s + "f"


def write_header(path: str, model: MLP, mean: np.ndarray, std: np.ndarray, t1: float, t2: float,
                 meta: dict) -> None:
    rows = ",\n".join("  { " + ", ".join(cf(x) for x in r) + " }" for r in model.W1)
    used = ", ".join(f"{FEATURE_NAME[f]}" for f in meta["features"])
    text = f"""#pragma once
/*
 * firmware/include/mlp_weights.h
 * ─────────────────────────────────────────────────────────────────────────────
 * ★ ml_model/train.py가 자동 생성한 파일입니다. 직접 수정하지 마세요. ★
 *
 * 학습 정보
 *   생성 시각      : {meta['created']}
 *   데이터셋       : {meta['dataset_name']} (sha256 {meta['dataset_sha256'][:16]}...)
 *   행 수(라벨별)  : 정상 {meta['counts'][0]} / 경고 {meta['counts'][1]} / 위험 {meta['counts'][2]}
 *   사용 특징      : {used}
 *   가스 기준값    : 예열 {meta['warmup']}샘플, 감쇠 {meta['decay']:.6g} (gas_baseline.h와 같아야 함)
 *   검증 macro-F1  : {meta['val_macro_f1']}   (단일 특징 규칙 기준선: {meta['baseline_val_macro_f1']})
 *   상세 기록      : ml_model/data/train_report.json
 * ─────────────────────────────────────────────────────────────────────────────
 */

// 1 = ml_model/train.py가 실제 데이터로 학습해 생성한 값, 0 = 학습되지 않은 임시값
#define MLP_WEIGHTS_TRAINED 1

// 입력 표준화: x_norm = (x_raw - MEAN) / STD
// 입력 순서: 온도[°C], 습도[%], 가스저항[kΩ], 기압[hPa], 가스비율(현재/기준값, 0~1)
// 사용하지 않는 특징은 가중치가 0입니다.
static const float MLP_MEAN[5] = {{ {', '.join(cf(x) for x in mean)} }};
static const float MLP_STD[5]  = {{ {', '.join(cf(x) for x in std)} }};

// 은닉층(5 노드): h[i] = ReLU( Σ_j x[j] * MLP_W_HIDDEN[i][j] + MLP_B_HIDDEN[i] )
static const float MLP_W_HIDDEN[5][5] = {{
{rows},
}};
static const float MLP_B_HIDDEN[5] = {{ {', '.join(cf(x) for x in model.b1)} }};

// 출력층(1 노드): score = Sigmoid( Σ_i h[i] * MLP_W_OUTPUT[i] + MLP_B_OUTPUT )
static const float MLP_W_OUTPUT[5] = {{ {', '.join(cf(x) for x in model.W2)} }};
static const float MLP_B_OUTPUT    = {cf(model.b2)};

// 점수 → QoS 매핑:  score >= MLP_TH_QOS2 → QoS 2,  score >= MLP_TH_QOS1 → QoS 1,  그 외 QoS 0
static const float MLP_TH_QOS1 = {cf(t1)};
static const float MLP_TH_QOS2 = {cf(t2)};
"""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


# ──────────────────────────────────────────────────────────────────────────────
# 실행
# ──────────────────────────────────────────────────────────────────────────────

def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def parse_features(text: str) -> List[str]:
    feats = [f.strip() for f in text.split(",") if f.strip()]
    if not feats or any(f not in FEATURE_ORDER for f in feats):
        raise SystemExit(f"--features 는 {FEATURE_ORDER} 중에서 쉼표로 고르세요 (입력: {text})")
    return [f for f in FEATURE_ORDER if f in feats]          # 펌웨어 순서로 정렬, 중복 제거


def compare_features(train_df: pd.DataFrame, val_df: pd.DataFrame, df: pd.DataFrame, args) -> None:
    """특징 조합별 검증 성능을 표로 비교합니다 (내보내기 없음)."""
    have_pres = float(df["pres_hpa"].abs().sum()) > 0
    combos = [["temp"], ["hum"], ["gas"], ["gasr"], ["temp", "gasr"], ["temp", "hum", "gasr"],
              ["temp", "gas"], ["temp", "hum", "gas"]]
    if have_pres:
        combos += [["pres"], ["temp", "hum", "gas", "pres"], ["temp", "hum", "gas", "pres", "gasr"]]
    else:
        combos += [["temp", "hum", "gas", "gasr"]]
    print("\n특징 조합별 성능 (같은 분할, 임계값은 학습 데이터에서 선택):")
    print(f"  {'특징':<28}{'학습 F1':>9}{'검증 F1':>9}{'검증 정확도':>12}   위험 재현율")
    rows = []
    for feats in combos:
        r = fit_and_eval(train_df, val_df, feats, args, restarts=min(args.restarts, 3), verbose=False)
        v = r["val"]
        rows.append((feats, r["train"]["macro_f1"], v["macro_f1"] if v else float("nan"),
                     v["accuracy"] if v else float("nan"), v["recall"]["2"] if v else None))
    for feats, ftr, fva, acc, rec2 in sorted(rows, key=lambda x: -(x[2] if x[2] == x[2] else -1)):
        name = "+".join(FEATURE_NAME[f] for f in feats)
        print(f"  {name:<{28 - sum(1 for c in name if ord(c) > 127)}}{ftr:>9.3f}{fva:>9.3f}{acc:>12.3f}   {rec2}")
    print("\n※ 표의 최고값은 검증 데이터로 골랐으므로 낙관적입니다. 고른 조합은 --val-sessions 로 새 세션에서 다시 확인하세요.\n"
          "※ 특징을 더했는데도 새 세션 성능이 오르지 않으면 쓸 이유가 없습니다 (가스저항 절대값·기압은 세션 간 편차 위험이 큼).")


def main() -> int:
    ap = argparse.ArgumentParser(description="위험 점수 MLP 학습 + 펌웨어 헤더 내보내기")
    ap.add_argument("--data", default=os.path.join(HERE, "data", "raw_dataset.csv"))
    ap.add_argument("--features", default=DEFAULT_FEATURES,
                    help=f"쉼표로 구분: {','.join(FEATURE_ORDER)} (기본: {DEFAULT_FEATURES})")
    ap.add_argument("--compare-features", action="store_true", help="특징 조합별 성능을 비교하고 종료 (내보내기 없음)")
    ap.add_argument("--val-frac", type=float, default=0.2)
    ap.add_argument("--val-sessions", default="", help="쉼표로 구분한 세션 ID 전체를 검증용으로 사용")
    ap.add_argument("--epochs", type=int, default=4000)
    ap.add_argument("--lr", type=float, default=0.03)
    ap.add_argument("--l2", type=float, default=1e-4)
    ap.add_argument("--restarts", type=int, default=6, help="서로 다른 초기값으로 학습해 검증 손실이 가장 낮은 것을 씁니다")
    ap.add_argument("--patience", type=int, default=600)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--min-per-class", type=int, default=30)
    ap.add_argument("--out-header", default=os.path.join(ROOT, "firmware", "include", "mlp_weights.h"))
    ap.add_argument("--out-model", default=os.path.join(HERE, "data", "mlp_model.npz"))
    ap.add_argument("--out-report", default=os.path.join(HERE, "data", "train_report.json"))
    ap.add_argument("--no-export", action="store_true", help="학습·평가만 하고 헤더는 쓰지 않습니다")
    ap.add_argument("--force", action="store_true", help="표본 부족/라벨 누락 검사를 무시하고 내보냅니다 (비권장)")
    args = ap.parse_args()

    feats = parse_features(args.features)
    df, info = load_dataset(args.data, feats)
    counts = [int((df["label"] == c).sum()) for c in (0, 1, 2)]
    print(f"데이터: 원본 {info['raw_rows']}행 → 학습 후보 {len(df)}행 | 세션 {df['session'].nunique()}개")
    print(f"  제외: 라벨 없음(-1) {info['unlabeled_rows']}행"
          + (f", 가스 기준값 예열 구간 {info['warmup_rows_dropped']}행 (부팅 후 {info['warmup_samples']}샘플)"
             if "gasr" in feats else "")
          + f", 유효하지 않은 행 {info['invalid_rows_dropped']}행")
    print("라벨별: " + " / ".join(f"{CLASS_NAMES[c]} {counts[c]}" for c in (0, 1, 2)))
    print("사용 특징: " + ", ".join(f"{FEATURE_NAME[f]}{('(' + FEATURE_UNIT[f] + ')') if FEATURE_UNIT[f] else ''}" for f in feats))

    problems = [f"{CLASS_NAMES[c]} 표본 {counts[c]}개 (최소 {args.min_per_class}개 필요)"
                for c in (0, 1, 2) if counts[c] < args.min_per_class]
    if problems:
        msg = "표본이 부족합니다:\n  - " + "\n  - ".join(problems)
        if not args.force and not args.compare_features:
            raise SystemExit(msg + "\n  → 더 수집하거나, 알고 있는 상태로 --force 를 쓰세요 (이 경우 논문에 한계를 명시해야 합니다).")
        print("경고: " + msg + ("\n  --force 로 계속합니다." if args.force else ""))

    val_sessions = [s.strip() for s in args.val_sessions.split(",") if s.strip()]
    train_df, val_df = split_blocks(df, args.val_frac, val_sessions or None)
    yva = val_df["label"].to_numpy(int)
    have_val = len(val_df) > 0 and len(set(yva)) == 3
    how = f"세션 {val_sessions} 전체를 검증용으로" if val_sessions else "세션·라벨별 시간순"
    print(f"분할({how}): 학습 {len(train_df)} / 검증 {len(val_df)}"
          + ("" if have_val else "  ⚠ 검증에 세 라벨이 모두 없어 검증 지표를 신뢰할 수 없습니다"))
    drift = session_drift_report(df, feats)

    if args.compare_features:
        compare_features(train_df, val_df, df, args)
        return 0

    res = fit_and_eval(train_df, val_df, feats, args, args.restarts, verbose=True)
    model, t1, t2, mlp_train, mlp_val = res["model"], res["t1"], res["t2"], res["train"], res["val"]
    rules = single_feature_rules(train_df, val_df, feats)
    best_rule = max(rules, key=lambda r: r["train_macro_f1"])       # 기준선은 학습 성능으로 고름 (검증 누수 방지)

    print("\n══════════════════════ 결과 ══════════════════════")
    print(f"QoS 임계값(학습 데이터에서 선택): QoS1 >= {t1:.3f} | QoS2 >= {t2:.3f}")
    print(f"학습  macro-F1 {mlp_train['macro_f1']:.3f}  정확도 {mlp_train['accuracy']:.3f}")
    if mlp_val:
        print(f"검증  macro-F1 {mlp_val['macro_f1']:.3f}  정확도 {mlp_val['accuracy']:.3f}  "
              f"라벨별 재현율 {mlp_val['recall']}")
        print("검증 혼동행렬 (행=실제 0/1/2, 열=예측 QoS 0/1/2):")
        for row in mlp_val["confusion"]:
            print("   ", row)
        print("\n[기준선] 특징 하나만 쓰는 임계값 규칙 (방향은 라벨과의 상관으로 판정):")
        for r in rules:
            mark = "  ← 학습 성능 최고 (기준선)" if r is best_rule else ""
            vf = r["val"]["macro_f1"] if r["val"] else float("nan")
            print(f"   {FEATURE_NAME[r['feature']]:<6} [{r['direction']}] 검증 macro-F1 {vf:.3f}  "
                  f"({r['text']}){mark}")
        base_val = best_rule["val"]
        gap = mlp_val["macro_f1"] - base_val["macro_f1"]
        # 검증 표본이 적으면 몇 %p 차이는 우연일 수 있습니다. 이항 분포 기준의 대략적 근사(3 표준오차 ≈ 1.5/√n)로
        # "의미 있는 차이"의 기준을 잡습니다. 이웃 샘플이 서로 비슷하므로 실제 유효 표본은 더 적어 이 값도 낙관적입니다.
        margin = max(0.03, 1.5 / np.sqrt(max(mlp_val["n"], 1)))
        print(f"[비교] MLP - 기준선 macro-F1 = {gap:+.3f}  (검증 {mlp_val['n']}개, 대략 ±{margin:.3f} 이내의 차이는 우연일 수 있음)  →  "
              + ("MLP가 가장 좋은 단일 특징 규칙보다 낫습니다 (여러 센서값을 함께 쓴 효과일 수 있음)." if gap > margin else
                 "단일 특징 규칙과 사실상 같습니다. 논문에서 ML의 기여를 과장하지 마세요(예: '임계값을 데이터로 보정'). "
                 if gap >= -margin else "단일 특징 규칙보다 나쁩니다. 데이터나 특징을 점검하세요."))
    else:
        base_val = None

    warmup, decay = load_constants()
    dataset_sha = sha256_of(args.data)
    report = {
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "dataset": {"path": os.path.relpath(args.data, ROOT), "sha256": dataset_sha, "rows": int(len(df)),
                    "sessions": int(df["session"].nunique()), "counts": {"0": counts[0], "1": counts[1], "2": counts[2]},
                    **info},
        "gas_baseline": {"warmup_samples": warmup, "decay": float(decay), "source": "firmware/include/gas_baseline.h"},
        "split": {"method": ("지정한 세션 전체를 검증용" if val_sessions else "세션·라벨별 시간순 뒤쪽 비율을 검증용으로 분리"),
                  "val_sessions": val_sessions, "val_frac": args.val_frac,
                  "train": int(len(train_df)), "val": int(len(val_df)), "val_has_all_classes": bool(have_val)},
        "features": feats, "soft_targets": SOFT_TARGET.tolist(),
        "hyperparameters": {"epochs": args.epochs, "lr": args.lr, "l2": args.l2, "restarts": args.restarts,
                            "patience": args.patience, "seed": args.seed, "hidden": N_HIDDEN, "inputs": N_IN},
        "restarts": res["log"], "thresholds": {"qos1": t1, "qos2": t2, "selected_on": "train"},
        "mlp": {"train": mlp_train, "val": mlp_val},
        "baseline_single_feature_rules": rules, "baseline_selected": best_rule["feature"],
        "session_drift": drift, "numpy": np.__version__,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out_report)), exist_ok=True)
    with open(args.out_report, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    np.savez(args.out_model, mean=res["mean"], std=res["std"], W1=model.W1, b1=model.b1, W2=model.W2,
             b2=np.array(model.b2), t1=t1, t2=t2)
    print(f"\n기록: {os.path.relpath(args.out_report, ROOT)}, {os.path.relpath(args.out_model, ROOT)}")

    if args.no_export:
        print("--no-export: 펌웨어 헤더는 쓰지 않았습니다.")
        return 0
    meta = {"created": report["created"], "dataset_name": os.path.basename(args.data),
            "dataset_sha256": dataset_sha, "counts": counts, "features": feats,
            "warmup": warmup, "decay": float(decay),
            "val_macro_f1": mlp_val["macro_f1"] if mlp_val else "N/A",
            "baseline_val_macro_f1": base_val["macro_f1"] if base_val else "N/A"}
    write_header(args.out_header, model, res["mean"], res["std"], t1, t2, meta)
    print(f"헤더 생성: {os.path.relpath(args.out_header, ROOT)}  (MLP_WEIGHTS_TRAINED = 1)")
    print("다음: python ml_model/verify_export.py  →  펌웨어 빌드/업로드")
    return 0


if __name__ == "__main__":
    sys.exit(main())
