"""
ml_model/train_threshold_calibration.py
─────────────────────────────────────────────────────────────────────────────
수집한 데이터(ml_model/data/raw_dataset.csv)로 온도/습도 QoS 임계값의 "보정폭(delta)"을
학습하고, 펌웨어용 헤더(firmware/include/qos_calibration.h)로 내보냅니다.

  python ml_model/train_threshold_calibration.py
  python ml_model/train_threshold_calibration.py --val-sessions 20260922-1030

★ 설계 원칙 — "사람이 정한 기준(SPEC)이 항상 이긴다" ★
  firmware/src/main_gingerbread.cpp의 SystemConfig 기본값(30/50/70/85)이 사람이 명시한
  SPEC입니다. 이 스크립트는 그 값을 대체할 새 임계값을 찾는 게 아니라, SPEC에서 얼마나
  벗어나야(delta) 데이터에 더 잘 맞는지를 아주 좁은 범위(--temp-limit/--hum-limit, 기본
  펌웨어의 TEMP_ADJUST_LIMIT_C/HUM_ADJUST_LIMIT_PCT와 동일한 ±3°C/±5%) 안에서만 찾습니다.
  펌웨어는 이 delta에 같은 clamp를 한 번 더 걸기 때문에, 이 스크립트가 범위를 넘는 값을
  내보내도 실제로는 적용되지 않습니다(이중 안전장치).

판단 로직 (firmware/src/main_gingerbread.cpp classify()/decide_qos_plan()과 동일)
  temp_sev = 0(정상) if temp <= temp_warn, 1(경고) if temp <= temp_danger, else 2(위험)
  hum_sev  = 위와 동일한 규칙을 습도에 적용
  최종 QoS = max(temp_sev, hum_sev)               (OR 판정 — 더 심각한 쪽을 따름)

탐색 방법
  temp_warn/temp_danger, hum_warn/hum_danger 각각의 delta를 grid로 촘촘히 훑어(기본
  온도 0.5°C, 습도 1.0% 간격) 학습 데이터에서 macro-F1이 최대인 조합을 찾습니다. 동점이면
  SPEC(delta=0)에 가장 가까운 조합을 고릅니다(불필요하게 SPEC에서 멀어지지 않도록).
  ml_model/train.py의 pick_thresholds()와 같은 "학습 데이터로 고르고 검증 데이터로 보고"
  원칙을 따릅니다.

정직한 평가를 위한 장치 (ml_model/train.py와 동일한 철학)
  • 분할: --val-sessions로 세션 전체를 검증용으로 떼거나, 없으면 세션·라벨별 시간순 뒤쪽
    20%를 검증용으로 씁니다.
  • SPEC(delta=0)을 기준선으로 두고, 학습된 delta가 실제로 더 나은지 검증 데이터에서
    비교합니다. 개선폭이 통계적으로 유의미하지 않으면(표본이 적을 때 특히) 그렇다고
    보고서에 명시합니다 — "학습했다"는 이유로 무조건 SPEC보다 낫다고 주장하지 않습니다.
  • 클래스별 표본이 너무 적으면 헤더를 내보내지 않습니다 (--force로 우회 가능).
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# SPEC — firmware/src/main_gingerbread.cpp의 SystemConfig 기본값과 반드시 같아야 합니다.
SPEC_TEMP_WARN_C   = 30.0
SPEC_TEMP_DANGER_C = 50.0
SPEC_HUM_WARN_PCT  = 70.0
SPEC_HUM_DANGER_PCT = 85.0

CLASS_NAMES = ["정상(QoS 0)", "경고(QoS 1)", "위험(QoS 2)"]


# ──────────────────────────────────────────────────────────────────────────────
# 데이터
# ──────────────────────────────────────────────────────────────────────────────

def load_dataset(path: str) -> pd.DataFrame:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        raise SystemExit(f"데이터가 없습니다: {path}\n  → ml_model/README.md의 수집 절차(collect.py)를 먼저 진행하세요.")
    df = pd.read_csv(path)
    need = {"session", "ms", "temp", "hum", "label"}
    if not need <= set(df.columns):
        raise SystemExit(f"CSV에 필요한 컬럼이 없습니다: {sorted(need - set(df.columns))}")
    for c in ("ms", "temp", "hum", "label"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["ms", "temp", "hum", "label"]).reset_index(drop=True)
    df["session"] = df["session"].astype(str)
    df = df[df["label"].isin([0, 1, 2])].copy()
    df["label"] = df["label"].astype(int)
    return df.reset_index(drop=True)


def split_blocks(df: pd.DataFrame, val_frac: float,
                 val_sessions: Optional[List[str]] = None) -> Tuple[pd.DataFrame, pd.DataFrame]:
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


# ──────────────────────────────────────────────────────────────────────────────
# 판단 로직 (펌웨어 classify()/decide_qos_plan()과 반드시 동일해야 함)
# ──────────────────────────────────────────────────────────────────────────────

def predict_qos(temp: np.ndarray, hum: np.ndarray,
                temp_warn: float, temp_danger: float,
                hum_warn: float, hum_danger: float) -> np.ndarray:
    temp_sev = np.where(temp > temp_danger, 2, np.where(temp > temp_warn, 1, 0))
    hum_sev  = np.where(hum > hum_danger, 2, np.where(hum > hum_warn, 1, 0))
    return np.maximum(temp_sev, hum_sev)


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


def confusion(y: np.ndarray, pred: np.ndarray) -> List[List[int]]:
    return [[int(((y == a) & (pred == b)).sum()) for b in (0, 1, 2)] for a in (0, 1, 2)]


def summarize(y: np.ndarray, pred: np.ndarray) -> dict:
    recall = {}
    for c in (0, 1, 2):
        n = int((y == c).sum())
        recall[str(c)] = round(float(((pred == c) & (y == c)).sum()) / n, 4) if n else None
    return {"n": int(len(y)), "accuracy": round(float((pred == y).mean()), 4),
            "macro_f1": round(macro_f1(y, pred), 4), "recall": recall, "confusion": confusion(y, pred)}


# ──────────────────────────────────────────────────────────────────────────────
# 4D grid search: (temp_warn_delta, temp_danger_delta, hum_warn_delta, hum_danger_delta)
# ──────────────────────────────────────────────────────────────────────────────

def search_deltas(temp: np.ndarray, hum: np.ndarray, y: np.ndarray,
                  temp_limit: float, hum_limit: float,
                  temp_step: float, hum_step: float) -> Tuple[Tuple[float, float, float, float], float]:
    """학습 데이터에서 macro-F1이 최대인 (온도warn, 온도danger, 습도warn, 습도danger) delta 조합을 찾습니다.
    동점이면 SPEC(delta=0,0,0,0)에 가장 가까운 조합을 고릅니다."""
    temp_deltas = np.round(np.arange(-temp_limit, temp_limit + 1e-9, temp_step), 6)
    hum_deltas  = np.round(np.arange(-hum_limit, hum_limit + 1e-9, hum_step), 6)

    best_f1 = -1.0
    best_combo = (0.0, 0.0, 0.0, 0.0)
    best_dist = float("inf")

    for tw in temp_deltas:
        for td in temp_deltas:
            t_warn, t_danger = SPEC_TEMP_WARN_C + tw, SPEC_TEMP_DANGER_C + td
            if t_danger < t_warn:
                continue  # SPEC 간격(20°C)이 clamp(최대 ±temp_limit*2)보다 훨씬 커서 보통 발생하지 않지만 방어적으로 skip
            temp_sev = np.where(temp > t_danger, 2, np.where(temp > t_warn, 1, 0))
            for hw in hum_deltas:
                for hd in hum_deltas:
                    h_warn, h_danger = SPEC_HUM_WARN_PCT + hw, SPEC_HUM_DANGER_PCT + hd
                    if h_danger < h_warn:
                        continue
                    hum_sev = np.where(hum > h_danger, 2, np.where(hum > h_warn, 1, 0))
                    pred = np.maximum(temp_sev, hum_sev)
                    f1 = macro_f1(y, pred)
                    dist = tw * tw + td * td + hw * hw + hd * hd  # SPEC과의 거리 (동점 타이브레이크)
                    if f1 > best_f1 + 1e-9 or (abs(f1 - best_f1) <= 1e-9 and dist < best_dist):
                        best_f1, best_combo, best_dist = f1, (float(tw), float(td), float(hw), float(hd)), dist

    return best_combo, best_f1


# ──────────────────────────────────────────────────────────────────────────────
# 내보내기
# ──────────────────────────────────────────────────────────────────────────────

def cf(v: float) -> str:
    s = f"{np.float32(v):.9g}"
    if "." not in s and "e" not in s and "inf" not in s and "nan" not in s:
        s += ".0"
    return s + "f"


def write_header(path: str, deltas: Tuple[float, float, float, float], meta: dict) -> None:
    tw, td, hw, hd = deltas
    text = f"""#pragma once
/*
 * firmware/include/qos_calibration.h
 * ─────────────────────────────────────────────────────────────────────────────
 * ★ ml_model/train_threshold_calibration.py가 자동 생성한 파일입니다. 직접 수정하지 마세요. ★
 *
 * 학습 정보
 *   생성 시각      : {meta['created']}
 *   데이터셋       : {meta['dataset_name']} (sha256 {meta['dataset_sha256'][:16]}...)
 *   행 수(라벨별)  : 정상 {meta['counts'][0]} / 경고 {meta['counts'][1]} / 위험 {meta['counts'][2]}
 *   검증 macro-F1  : {meta['val_macro_f1']}  (SPEC delta=0 기준선: {meta['baseline_val_macro_f1']})
 *   상세 기록      : ml_model/data/threshold_calibration_report.json
 *
 * 설계 원칙 — "사람이 정한 기준이 항상 이긴다"
 *   SPEC(main_gingerbread.cpp SystemConfig 기본값 30/50/70/85)이 최종 결정권을 가집니다.
 *   아래 delta는 그 위에 더해지는 작은 보정치일 뿐이며, 펌웨어의 TEMP_ADJUST_LIMIT_C/
 *   HUM_ADJUST_LIMIT_PCT로 항상 다시 clamp됩니다(이 헤더 값이 손상되어도 안전).
 * ─────────────────────────────────────────────────────────────────────────────
 */

// 1 = ml_model/train_threshold_calibration.py가 실제 데이터로 학습해 생성한 값, 0 = 학습 전(delta=0)
#define QOS_CALIBRATION_TRAINED 1

// SPEC(main_gingerbread.cpp SystemConfig 기본값) 대비 보정폭. 단위는 각각 °C, %.
static const float QOS_CAL_TEMP_WARN_DELTA_C    = {cf(tw)};
static const float QOS_CAL_TEMP_DANGER_DELTA_C  = {cf(td)};
static const float QOS_CAL_HUM_WARN_DELTA_PCT   = {cf(hw)};
static const float QOS_CAL_HUM_DANGER_DELTA_PCT = {cf(hd)};
"""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


# ──────────────────────────────────────────────────────────────────────────────
# 실행
# ──────────────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="QoS 온도/습도 임계값 보정폭(delta) 학습 + 펌웨어 헤더 내보내기")
    ap.add_argument("--data", default=os.path.join(HERE, "data", "raw_dataset.csv"))
    ap.add_argument("--val-frac", type=float, default=0.2)
    ap.add_argument("--val-sessions", default="", help="쉼표로 구분한 세션 ID 전체를 검증용으로 사용")
    ap.add_argument("--temp-limit", type=float, default=3.0, help="온도 delta 탐색 범위 (± °C, 펌웨어 TEMP_ADJUST_LIMIT_C와 일치해야 함)")
    ap.add_argument("--hum-limit", type=float, default=5.0, help="습도 delta 탐색 범위 (± %%, 펌웨어 HUM_ADJUST_LIMIT_PCT와 일치해야 함)")
    ap.add_argument("--temp-step", type=float, default=0.5, help="온도 delta 탐색 간격 (°C)")
    ap.add_argument("--hum-step", type=float, default=1.0, help="습도 delta 탐색 간격 (%%)")
    ap.add_argument("--min-per-class", type=int, default=30)
    ap.add_argument("--out-header", default=os.path.join(ROOT, "firmware", "include", "qos_calibration.h"))
    ap.add_argument("--out-report", default=os.path.join(HERE, "data", "threshold_calibration_report.json"))
    ap.add_argument("--no-export", action="store_true", help="학습·평가만 하고 헤더는 쓰지 않습니다")
    ap.add_argument("--force", action="store_true", help="표본 부족 검사를 무시하고 내보냅니다 (비권장)")
    args = ap.parse_args()

    df = load_dataset(args.data)
    counts = [int((df["label"] == c).sum()) for c in (0, 1, 2)]
    print(f"데이터: {len(df)}행 | 세션 {df['session'].nunique()}개")
    print("라벨별: " + " / ".join(f"{CLASS_NAMES[c]} {counts[c]}" for c in (0, 1, 2)))

    problems = [f"{CLASS_NAMES[c]} 표본 {counts[c]}개 (최소 {args.min_per_class}개 필요)"
                for c in (0, 1, 2) if counts[c] < args.min_per_class]
    if problems:
        msg = "표본이 부족합니다:\n  - " + "\n  - ".join(problems)
        if not args.force:
            raise SystemExit(msg + "\n  → 더 수집하거나, 알고 있는 상태로 --force 를 쓰세요.")
        print("경고: " + msg + " -- --force 로 계속합니다.")

    val_sessions = [s.strip() for s in args.val_sessions.split(",") if s.strip()]
    train_df, val_df = split_blocks(df, args.val_frac, val_sessions or None)
    yva = val_df["label"].to_numpy(int)
    have_val = len(val_df) > 0 and len(set(yva)) == 3
    how = f"세션 {val_sessions} 전체를 검증용으로" if val_sessions else "세션·라벨별 시간순"
    print(f"분할({how}): 학습 {len(train_df)} / 검증 {len(val_df)}"
          + ("" if have_val else "  ⚠ 검증에 세 라벨이 모두 없어 검증 지표를 신뢰할 수 없습니다"))

    temp_tr, hum_tr, y_tr = train_df["temp"].to_numpy(float), train_df["hum"].to_numpy(float), train_df["label"].to_numpy(int)
    temp_va, hum_va, y_va = val_df["temp"].to_numpy(float), val_df["hum"].to_numpy(float), val_df["label"].to_numpy(int)

    deltas, train_f1 = search_deltas(temp_tr, hum_tr, y_tr,
                                     args.temp_limit, args.hum_limit,
                                     args.temp_step, args.hum_step)
    tw, td, hw, hd = deltas
    t_warn, t_danger = SPEC_TEMP_WARN_C + tw, SPEC_TEMP_DANGER_C + td
    h_warn, h_danger = SPEC_HUM_WARN_PCT + hw, SPEC_HUM_DANGER_PCT + hd

    pred_tr = predict_qos(temp_tr, hum_tr, t_warn, t_danger, h_warn, h_danger)
    cal_train = summarize(y_tr, pred_tr)

    base_pred_tr = predict_qos(temp_tr, hum_tr, SPEC_TEMP_WARN_C, SPEC_TEMP_DANGER_C, SPEC_HUM_WARN_PCT, SPEC_HUM_DANGER_PCT)
    base_train = summarize(y_tr, base_pred_tr)

    cal_val = base_val = None
    if len(val_df):
        pred_va = predict_qos(temp_va, hum_va, t_warn, t_danger, h_warn, h_danger)
        cal_val = summarize(y_va, pred_va)
        base_pred_va = predict_qos(temp_va, hum_va, SPEC_TEMP_WARN_C, SPEC_TEMP_DANGER_C, SPEC_HUM_WARN_PCT, SPEC_HUM_DANGER_PCT)
        base_val = summarize(y_va, base_pred_va)

    print("\n══════════════════════ 결과 ══════════════════════")
    print(f"학습된 delta — 온도: warn {tw:+.1f}°C, danger {td:+.1f}°C | 습도: warn {hw:+.1f}%, danger {hd:+.1f}%")
    print(f"보정된 임계값 — 온도: {t_warn:.1f}/{t_danger:.1f}°C | 습도: {h_warn:.1f}/{h_danger:.1f}%")
    print(f"학습 macro-F1 — 보정: {cal_train['macro_f1']:.3f} | SPEC(delta=0): {base_train['macro_f1']:.3f}")
    if cal_val:
        gap = cal_val["macro_f1"] - base_val["macro_f1"]
        margin = max(0.03, 1.5 / np.sqrt(max(cal_val["n"], 1)))
        print(f"검증 macro-F1 — 보정: {cal_val['macro_f1']:.3f} | SPEC(delta=0): {base_val['macro_f1']:.3f}  "
              f"(차이 {gap:+.3f}, 검증 {cal_val['n']}개, 대략 ±{margin:.3f} 이내는 우연일 수 있음)")
        print("  → " + ("학습된 보정이 SPEC보다 유의미하게 낫습니다." if gap > margin else
                        "SPEC과 사실상 같습니다 — 보정의 실효성을 과장하지 마세요." if gap >= -margin else
                        "SPEC보다 오히려 나쁩니다 — 데이터나 --temp-limit/--hum-limit 설정을 점검하세요."))
    else:
        print("⚠ 검증 데이터가 부족해 SPEC 대비 비교를 신뢰할 수 없습니다.")

    dataset_sha = sha256_of(args.data)
    report = {
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "dataset": {"path": os.path.relpath(args.data, ROOT), "sha256": dataset_sha, "rows": int(len(df)),
                    "sessions": int(df["session"].nunique()), "counts": {"0": counts[0], "1": counts[1], "2": counts[2]}},
        "spec": {"temp_warn_c": SPEC_TEMP_WARN_C, "temp_danger_c": SPEC_TEMP_DANGER_C,
                 "hum_warn_pct": SPEC_HUM_WARN_PCT, "hum_danger_pct": SPEC_HUM_DANGER_PCT},
        "search": {"temp_limit_c": args.temp_limit, "hum_limit_pct": args.hum_limit,
                   "temp_step_c": args.temp_step, "hum_step_pct": args.hum_step},
        "split": {"method": ("지정한 세션 전체를 검증용" if val_sessions else "세션·라벨별 시간순 뒤쪽 비율을 검증용으로 분리"),
                  "val_sessions": val_sessions, "val_frac": args.val_frac,
                  "train": int(len(train_df)), "val": int(len(val_df)), "val_has_all_classes": bool(have_val)},
        "deltas": {"temp_warn": tw, "temp_danger": td, "hum_warn": hw, "hum_danger": hd},
        "calibrated": {"train": cal_train, "val": cal_val},
        "spec_baseline": {"train": base_train, "val": base_val},
        "numpy": np.__version__,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out_report)), exist_ok=True)
    with open(args.out_report, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n기록: {os.path.relpath(args.out_report, ROOT)}")

    if args.no_export:
        print("--no-export: 펌웨어 헤더는 쓰지 않았습니다.")
        return 0

    meta = {"created": report["created"], "dataset_name": os.path.basename(args.data),
            "dataset_sha256": dataset_sha, "counts": counts,
            "val_macro_f1": cal_val["macro_f1"] if cal_val else "N/A",
            "baseline_val_macro_f1": base_val["macro_f1"] if base_val else "N/A"}
    write_header(args.out_header, deltas, meta)
    print(f"헤더 생성: {os.path.relpath(args.out_header, ROOT)}  (QOS_CALIBRATION_TRAINED = 1)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
