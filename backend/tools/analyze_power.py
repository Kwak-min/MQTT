"""
backend/tools/analyze_power.py
─────────────────────────────────────────────────────────────────────────────
실험 결과(backend/logs/power_ext.csv) 분석: 두 노드(Gingerbread vs Standard)의 에너지, 지연, QoS·전송 계층,
네트워크 혼잡을 표와 신뢰구간으로 정리하고, 결론이 모델 가정에 얼마나 좌우되는지 민감도를 봅니다.

  python backend/tools/analyze_power.py                       # 기본: backend/logs/power_ext.csv
  python backend/tools/analyze_power.py --csv 내로그.csv --skip-first 24 --sensitivity
  python backend/tools/analyze_power.py --md 결과.md          # 마크다운으로도 저장

옵션
  --a / --b          비교할 두 노드를 client_id 부분 문자열로 지정 (기본: Gingerbread / Standard, 대소문자 무시)
  --skip-first N     노드마다 앞의 N사이클을 버림 (부팅 직후·예열 구간 제외)
  --boot N           부트스트랩 반복 횟수 (기본 2000)
  --sensitivity      실측 로그의 원시 입력으로 IDLE_MA 등을 바꿔 다시 계산 (Sleep 해석 두 가지 포함)

핵심 원칙
  · 두 노드는 사이클 길이가 다르므로 "사이클당 에너지"가 아니라 "평균 전류(= 총 에너지 / 총 시간 / 전압)"로 비교합니다.
  · 모든 수치는 소프트웨어 모델의 "추정값"입니다. 실측 전류로 모델을 검증하기 전에는 절대값을 주장하지 마세요.
  · 신뢰구간은 사이클을 독립으로 보고 부트스트랩합니다. 이웃 사이클은 서로 닮아 있어(자기상관) 실제 불확실성은
    더 큽니다. 실험을 여러 번(세션) 반복해서 세션 단위로 보고하는 것이 가장 정직합니다.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
sys.path.insert(0, BACKEND)

logging.disable(logging.CRITICAL)
import app.services.power_estimator as pe  # noqa: E402

DEFAULT_CSV = os.path.join(BACKEND, "logs", "power_ext.csv")
NEEDED = ["client_id", "qos", "rtt_ms", "retry_count", "act_ms", "slp_ms", "estimated_energy_mwh"]
MS_PER_H = 3_600_000.0


# ──────────────────────────────────────────────────────────────────────────────
# 데이터
# ──────────────────────────────────────────────────────────────────────────────

def load(path: str) -> pd.DataFrame:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        raise SystemExit(f"데이터가 없습니다: {path}\n  → 게이트웨이를 실행해 두 노드의 실험 데이터를 먼저 수집하세요.")
    df = pd.read_csv(path)
    miss = [c for c in NEEDED if c not in df.columns]
    if miss:
        raise SystemExit(f"CSV에 필요한 컬럼이 없습니다: {miss}\n  (power_ext.csv 가 아닌 구버전 power.csv 를 넣지 않았는지 확인하세요)")
    for c in NEEDED[1:] + ["net_loss_pct", "rtt_ratio", "congested", "probe", "average_current_ma",
                           "active_energy_mwh", "idle_energy_mwh", "sleep_energy_mwh"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    n0 = len(df)
    df = df.dropna(subset=["qos", "act_ms", "slp_ms", "estimated_energy_mwh"]).copy()
    df["cycle_ms"] = df["act_ms"] + df["slp_ms"]
    df = df[df["cycle_ms"] > 0]
    if len(df) < n0:
        print(f"(유효하지 않은 행 {n0 - len(df)}개 제외)")
    return df.reset_index(drop=True)


def pick(df: pd.DataFrame, key: str) -> pd.DataFrame:
    """
    client_id 에 key 가 (대소문자 무시) 포함된 행. 실제 장비의 ID는 ConnectPacket의 16바이트 제한으로 잘려 기록되므로
    ("ESP32-Gingerbread" → "ESP32-Gingerbrea") key 의 앞 16자가 ID의 일부이거나 ID가 key 의 앞부분이어도 찾습니다.
    """
    ids = df["client_id"].astype(str)
    k = key.lower()

    def matches(cid: str) -> bool:
        s = cid.lower()
        if k in s:
            return True
        # ID가 16바이트로 잘려 키워드의 끝이 없어진 경우: 키워드의 앞부분(최소 6자, 최대 2자 짧게)이 ID에 들어 있으면 일치
        return any(k[:n] in s for n in range(len(k) - 1, max(len(k) - 3, 5), -1))

    sub = df[ids.apply(matches)]
    if sub.empty:
        raise SystemExit(f"'{key}' 를 포함하는 client_id 가 없습니다. 데이터의 노드: {sorted(ids.unique())}\n"
                         "  --a / --b 로 지정하세요 (예: --a Gingerbrea --b Standard).")
    return sub


def skip_first(df: pd.DataFrame, n: int) -> pd.DataFrame:
    return df.groupby("client_id", sort=False).apply(lambda g: g.iloc[n:], include_groups=False).reset_index(level=0) if n > 0 else df


# ──────────────────────────────────────────────────────────────────────────────
# 통계
# ──────────────────────────────────────────────────────────────────────────────

def avg_current_ma(energy_mwh: np.ndarray, cycle_ms: np.ndarray) -> float:
    """평균 전류(mA) = 총 에너지 / 공급 전압 / 총 시간. (사이클 길이가 다른 노드를 공정하게 비교)"""
    return float(energy_mwh.sum() / pe.VCC_V / (cycle_ms.sum() / MS_PER_H))


def boot_ci(stat, arrays: Tuple[np.ndarray, ...], n_boot: int, rng, alpha: float = 0.05):
    """사이클을 복원추출하는 부트스트랩 백분위 신뢰구간. arrays 는 같은 길이의 배열 묶음(행 단위로 함께 추출)."""
    n = len(arrays[0])
    vals = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, n, n)
        vals[i] = stat(*(a[idx] for a in arrays))
    return float(np.quantile(vals, alpha / 2)), float(np.quantile(vals, 1 - alpha / 2))


def ratio_ci(a: pd.DataFrame, b: pd.DataFrame, energy_col: str, n_boot: int, rng):
    """평균 전류의 비율 (a / b) 과 95% 부트스트랩 신뢰구간. 두 노드를 독립으로 재추출."""
    ea, ca = a[energy_col].to_numpy(float), a["cycle_ms"].to_numpy(float)
    eb, cb = b[energy_col].to_numpy(float), b["cycle_ms"].to_numpy(float)
    point = avg_current_ma(ea, ca) / avg_current_ma(eb, cb)
    vals = np.empty(n_boot)
    for i in range(n_boot):
        ia, ib = rng.integers(0, len(ea), len(ea)), rng.integers(0, len(eb), len(eb))
        vals[i] = avg_current_ma(ea[ia], ca[ia]) / avg_current_ma(eb[ib], cb[ib])
    return point, float(np.quantile(vals, 0.025)), float(np.quantile(vals, 0.975))


# ──────────────────────────────────────────────────────────────────────────────
# 출력 도우미 (콘솔 + 마크다운)
# ──────────────────────────────────────────────────────────────────────────────

class Out:
    def __init__(self) -> None:
        self.md: List[str] = []

    def h(self, text: str) -> None:
        print(f"\n{text}\n{'─' * 78}")
        self.md += ["", f"## {text}", ""]

    def p(self, text: str = "") -> None:
        print(text)
        self.md.append(text)

    def table(self, header: List[str], rows: List[List[str]]) -> None:
        widths = [max(len(str(x)) + sum(1 for ch in str(x) if ord(ch) > 127) for x in col) for col in zip(header, *rows)]
        def fmt(r):
            return "  ".join(str(x).ljust(w - sum(1 for ch in str(x) if ord(ch) > 127)) for x, w in zip(r, widths))
        print(fmt(header))
        for r in rows:
            print(fmt(r))
        self.md.append("| " + " | ".join(header) + " |")
        self.md.append("|" + "|".join("---" for _ in header) + "|")
        for r in rows:
            self.md.append("| " + " | ".join(str(x) for x in r) + " |")


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


# ──────────────────────────────────────────────────────────────────────────────
# 분석 섹션
# ──────────────────────────────────────────────────────────────────────────────

def overview(out: Out, nodes: Dict[str, pd.DataFrame]) -> None:
    out.h("1. 개요 (노드별)")
    rows = []
    for name, d in nodes.items():
        span_h = d["cycle_ms"].sum() / MS_PER_H
        q = d["qos"].value_counts(normalize=True)
        tp = d["transport"].value_counts(normalize=True) if "transport" in d.columns else pd.Series(dtype=float)
        retry_rate = float((d["retry_count"] > 0).mean())
        rows.append([name, f"{len(d)}", f"{span_h:.2f} h", f"{d['cycle_ms'].mean() / 1000:.2f} s",
                     " / ".join(f"Q{k}:{pct(q.get(k, 0.0))}" for k in (0, 1, 2)),
                     " / ".join(f"{k}:{pct(v)}" for k, v in tp.items()) or "-",
                     pct(retry_rate)])
    out.table(["노드", "사이클 수", "총 시간", "평균 사이클", "QoS 분포", "전송 계층", "재전송 있는 사이클"], rows)


def energy(out: Out, a_name: str, b_name: str, a: pd.DataFrame, b: pd.DataFrame, n_boot: int, rng) -> None:
    out.h("2. 에너지 (소프트웨어 모델 추정값, 평균 전류 기준)")
    rows = []
    for name, d in ((a_name, a), (b_name, b)):
        e, c = d["estimated_energy_mwh"].to_numpy(float), d["cycle_ms"].to_numpy(float)
        cur = avg_current_ma(e, c)
        lo, hi = boot_ci(avg_current_ma, (e, c), n_boot, rng)
        share = ""
        if {"active_energy_mwh", "idle_energy_mwh", "sleep_energy_mwh"} <= set(d.columns):
            tot = d[["active_energy_mwh", "idle_energy_mwh", "sleep_energy_mwh"]].sum()
            s = tot / tot.sum()
            share = f"TX/RX {pct(s.iloc[0])} · 대기 {pct(s.iloc[1])} · Sleep {pct(s.iloc[2])}"
        rows.append([name, f"{len(d)}", f"{cur:.3f} mA", f"[{lo:.3f}, {hi:.3f}]",
                     f"{d['estimated_energy_mwh'].mean() * 1000:.2f} μWh", share])
    out.table(["노드", "사이클", "평균 전류", "95% CI", "사이클당 에너지", "에너지 구성"], rows)
    r, lo, hi = ratio_ci(a, b, "estimated_energy_mwh", n_boot, rng)
    out.p(f"\n평균 전류 비 ({a_name} / {b_name}) = {r:.3f}   95% CI [{lo:.3f}, {hi:.3f}]   →  "
          f"{a_name}는 {b_name}의 {pct(r)} 수준 (절감 {pct(1 - r)})")
    if lo <= 1.0 <= hi:
        out.p("※ 신뢰구간이 1을 포함합니다: 두 노드의 평균 전류 차이가 통계적으로 확실하지 않습니다.")
    out.p("※ 이 값은 모델 추정입니다. 실측 전류로 검증하기 전에는 '절감률'로 주장하지 말고 '추정 절감률'로 쓰세요.")


def latency(out: Out, nodes: Dict[str, pd.DataFrame]) -> None:
    out.h("3. 지연(RTT)과 재전송 — ACK를 받는 전송(QoS 1·2)만")
    rows = []
    for name, d in nodes.items():
        acked = d[(d["qos"] >= 1) & (d["rtt_ms"] > 0)]
        if acked.empty:
            rows.append([name, "-", "-", "-", "-", "-", "-"])
            continue
        keys = ["qos"] + (["transport"] if "transport" in acked.columns else [])
        for k, g in acked.groupby(keys):
            k = k if isinstance(k, tuple) else (k,)
            label = f"QoS{int(k[0])}" + (f"/{k[1]}" if len(k) > 1 else "")
            r = g["rtt_ms"]
            rows.append([name, label, f"{len(g)}", f"{r.median():.1f}", f"{r.quantile(.95):.1f}", f"{r.max():.1f}",
                         f"{g['retry_count'].mean():.3f}"])
    out.table(["노드", "구분", "n", "RTT 중앙값(ms)", "p95(ms)", "최대(ms)", "평균 재전송"], rows)
    out.p("\n※ RTT는 최초 전송~ACK 수신이라 재전송 대기(타임아웃)를 포함합니다. QoS 0의 RTT는 로컬 전송 시간이라 표에서 뺐습니다.")
    out.p("※ Gingerbread의 TCP(QoS 2) RTT에는 연결 설정 시간이 포함됩니다 (시도마다 연결하는 구현).")


def congestion(out: Out, a_name: str, a: pd.DataFrame) -> None:
    if "congested" not in a.columns or a["congested"].notna().sum() == 0:
        return
    out.h(f"4. 네트워크 혼잡 판정 ({a_name}, 혼잡도 지표를 보고하는 노드만)")
    c = a.dropna(subset=["congested"])
    rows = [[f"{len(c)}", pct(float(c["congested"].mean())),
             pct(float(c["probe"].mean())) if "probe" in c else "-",
             f"{c['net_loss_pct'].mean():.2f}%" if "net_loss_pct" in c else "-",
             f"{c['rtt_ratio'].mean():.2f}" if "rtt_ratio" in c else "-",
             f"{int((c['congested'].diff().abs() > 0).sum())}"]]
    out.table(["사이클", "혼잡 판정 시간", "프로브 사이클", "평균 손실 EWMA", "평균 지연 배율", "혼잡 상태 전이"], rows)
    out.p("\n※ 혼잡 판정 시간과 상태 전이는 실험에서 인위적으로 가한 손실·지연 조건(tc netem 등)과 대조해서 해석하세요.")


def sensitivity(out: Out, a_name: str, b_name: str, a: pd.DataFrame, b: pd.DataFrame) -> None:
    """실측 로그의 원시 입력으로 모델 상수를 바꿔 다시 계산합니다 (평균 전류 비 a/b)."""
    out.h("5. 민감도: 결론이 모델 가정에 얼마나 좌우되는가 (같은 로그, 상수만 변경)")

    def total_current(d: pd.DataFrame, sleep_as_idle: bool) -> float:
        e = 0.0
        for _, r in d.iterrows():
            act, slp = float(r["act_ms"]), float(r["slp_ms"])
            if sleep_as_idle:
                act, slp = act + slp, 0.0            # Sleep 구간에도 무선이 켜져 있었다면 (delay() 대기)
            e += pe.estimate_cycle_energy(int(r["qos"]), float(r["rtt_ms"]), int(r["retry_count"]), act, slp,
                                          timeout_wait_ms=float(r["retry_count"]) * pe.ACK_TIMEOUT_MS)["total_energy_mwh"]
        return e / pe.VCC_V / (d["cycle_ms"].sum() / MS_PER_H)

    saved = {k: getattr(pe, k) for k in ("IDLE_MA", "SLEEP_MA", "RETRY_PENALTY")}
    rows = []
    try:
        for idle in (10.0, 20.0, 50.0, 80.0, 100.0):
            pe.IDLE_MA = idle
            ra = total_current(a, False) / total_current(b, False)
            rb = total_current(a, True) / total_current(b, True)
            rows.append([f"{idle:.0f}", f"{ra:.3f}", pct(1 - ra), f"{rb:.3f}", pct(1 - rb)])
    finally:
        for k, v in saved.items():
            setattr(pe, k, v)
    out.table(["IDLE_MA(mA)", f"전류 비 {a_name}/{b_name}", "추정 절감", "Sleep을 대기로 봤을 때 비", "추정 절감"], rows)
    out.p("\n해석: 오른쪽 두 열은 '노드가 실제로는 Sleep하지 않고 무선을 켠 채 기다렸다면'의 결과입니다.")
    out.p("      두 해석의 차이가 크면 절감은 시스템이 아니라 Sleep 구현 여부에 좌우됩니다 (실제 Sleep 구현과 전류 실측으로 확인).")


def caveats(out: Out) -> None:
    out.h("보고 시 반드시 밝힐 한계")
    for line in (
        "1. 에너지는 소프트웨어 모델의 추정값입니다 (TX/RX 전류, IDLE_MA, RETRY_PENALTY 등은 가정값이며 실측 보정 전).",
        "2. 신뢰구간은 사이클을 독립으로 가정한 부트스트랩이라 실제 불확실성보다 좁습니다. 세션(반복 실험) 단위로 보고하세요.",
        "3. 두 노드의 프로토콜 오버헤드(TCP vs UDP 핸드셰이크·ACK·킵얼라이브)는 모델에 별도 항이 없습니다.",
        "4. 실험 조건(네트워크 손실/지연, 환경 자극, 기간)과 표본 수를 함께 적으세요.",
    ):
        out.p(line)


# ──────────────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="두 노드의 전력·지연·혼잡 분석 (부트스트랩 신뢰구간, 민감도)")
    ap.add_argument("--csv", default=DEFAULT_CSV)
    ap.add_argument("--a", default="Gingerbread", help="비교 노드 A (client_id 부분 문자열)")
    ap.add_argument("--b", default="Standard", help="비교 노드 B (client_id 부분 문자열)")
    ap.add_argument("--skip-first", type=int, default=0, help="노드마다 앞의 N사이클 제외")
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--sensitivity", action="store_true")
    ap.add_argument("--md", default="", help="마크다운 결과 저장 경로")
    args = ap.parse_args()

    df = skip_first(load(args.csv), args.skip_first)
    a, b = pick(df, args.a), pick(df, args.b)
    if set(a["client_id"]) & set(b["client_id"]):
        raise SystemExit("--a 와 --b 가 같은 노드를 가리킵니다. 서로 다른 부분 문자열을 지정하세요.")
    for name, d in ((args.a, a), (args.b, b)):
        if len(d) < 30:
            print(f"⚠ {name}: 사이클이 {len(d)}개뿐입니다. 신뢰구간이 매우 넓거나 무의미합니다 (권장 수백 개 이상).")
    rng = np.random.default_rng(args.seed)
    out = Out()
    print(f"데이터: {args.csv} | {args.a}: {len(a)}사이클 ({', '.join(sorted(set(a['client_id'].astype(str))))}) "
          f"| {args.b}: {len(b)}사이클 ({', '.join(sorted(set(b['client_id'].astype(str))))})")
    nodes = {args.a: a, args.b: b}
    overview(out, nodes)
    energy(out, args.a, args.b, a, b, args.boot, rng)
    latency(out, nodes)
    congestion(out, args.a, a)
    if args.sensitivity:
        sensitivity(out, args.a, args.b, a, b)
    caveats(out)
    if args.md:
        with open(args.md, "w", encoding="utf-8") as f:
            f.write("# 전력 분석 결과\n\n" + "\n".join(out.md) + "\n")
        print(f"\n마크다운 저장: {args.md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
