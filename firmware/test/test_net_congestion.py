"""
firmware/test/test_net_congestion.py
─────────────────────────────────────────────────────────────────────────────
firmware/include/net_congestion.h(네트워크 혼잡도 추적기)를 PC의 g++로 컴파일해 시나리오별로 동작을 검증합니다.
보드가 필요 없습니다.

  python firmware/test/test_net_congestion.py

검증하는 것: 정상 네트워크에서 오탐 없음, 손실 증가 시 진입, 회복 시 해제(히스테리시스), 지연 급증 시 진입,
             프로브 주기, 경계/방어 동작, 손실 상한 설정 변경 반영.
※ 이것은 "구현이 설계대로 동작하는지"의 검증이며, 상수(임계값, 계수)가 실제 네트워크에서 적절한지는 아닙니다.
   그건 손실·지연을 인위적으로 만든 실기 실험(tc netem 등)으로 보정해야 합니다.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
INCLUDE = os.path.join(os.path.dirname(HERE), "include")


def header_const(name: str, cast=float):
    """net_congestion.h의 상수를 읽습니다 (값을 바꿔도 시험이 어긋나지 않도록 하드코딩하지 않음)."""
    text = open(os.path.join(INCLUDE, "net_congestion.h"), encoding="utf-8").read()
    m = re.search(rf"#define\s+{name}\s+([0-9.eE+-]+)f?", text)
    if not m:
        raise SystemExit(f"net_congestion.h 에서 {name} 을 찾지 못했습니다")
    return cast(m.group(1))


PROBE = header_const("NET_PROBE_INTERVAL", int)
ALPHA = header_const("NET_LOSS_ALPHA", float)

# 명령 스트림을 읽어 추적기를 구동하고 매 단계의 상태를 출력하는 하네스
#   "O attempts failures rtt limit" : net_observe     "I" : net_on_idle_cycle     "R" : reset
#   출력: loss rtt_ratio degraded probe_due idle_cycles
HARNESS = r"""
#include <stdio.h>
#include <string.h>
#include "net_congestion.h"
int main() {
  NetCongestion n; net_congestion_reset(n);
  char op[8];
  while (scanf("%7s", op) == 1) {
    if (op[0] == 'O') {
      unsigned a, f; float r, lim;
      if (scanf("%u %u %f %f", &a, &f, &r, &lim) != 4) break;
      net_observe(n, a, f, r, lim);
    } else if (op[0] == 'I') {
      net_on_idle_cycle(n);
    } else if (op[0] == 'R') {
      net_congestion_reset(n);
    } else if (op[0] == 'D') {                       // D 환경위험(0/1) 환경경고(0/1) RSSI약함(0/1) → "DEC qos probe"
      int c, w, r;
      if (scanf("%d %d %d", &c, &w, &r) != 3) break;
      const QosDecision d = net_decide_qos(c != 0, w != 0, r != 0, n);
      printf("DEC %d %d\n", d.qos, d.probe ? 1 : 0);
      fflush(stdout);
      continue;
    }
    printf("%.6f %.6f %d %d %u\n", n.loss_pct, net_rtt_ratio(n), n.degraded ? 1 : 0, net_probe_due(n) ? 1 : 0, (unsigned)n.idle_cycles);
    fflush(stdout);
  }
  return 0;
}
"""


class Tracker:
    """하네스를 한 번 컴파일하고, 명령 목록을 실행해 단계별 상태를 돌려줍니다."""

    def __init__(self) -> None:
        cxx = shutil.which("g++") or shutil.which("c++")
        if not cxx:
            raise SystemExit("g++ 를 찾지 못했습니다 (이 시험에 필요).")
        self._tmp = tempfile.TemporaryDirectory()
        src = os.path.join(self._tmp.name, "h.cpp")
        self.exe = os.path.join(self._tmp.name, "h.exe")
        open(src, "w").write(HARNESS)
        r = subprocess.run([cxx, "-O2", "-Wall", "-Wextra", "-I", INCLUDE, src, "-o", self.exe],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit("net_congestion.h 컴파일 실패:\n" + r.stderr)
        if r.stderr.strip():
            print("컴파일 경고:\n" + r.stderr)

    def run(self, cmds):
        out = subprocess.run([self.exe], input="\n".join(cmds), capture_output=True, text=True).stdout.strip().splitlines()
        assert len(out) == len(cmds), f"출력 {len(out)}개 != 명령 {len(cmds)}개"
        rows = [o.split() for o in out]
        return [{"loss": float(r[0]), "ratio": float(r[1]), "deg": r[2] == "1", "probe": r[3] == "1", "idle": int(r[4])}
                for r in rows]


def obs(attempts=1, failures=0, rtt=20.0, limit=5.0):
    return f"O {attempts} {failures} {rtt} {limit}"


class Live:
    """하네스를 대화형으로 구동합니다 (판정 결과에 따라 다음 입력이 달라지는 시스템 시뮬레이션용)."""

    def __init__(self, tracker: Tracker) -> None:
        self.p = subprocess.Popen([tracker.exe], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)

    def send(self, line: str) -> list:
        self.p.stdin.write(line + "\n")
        self.p.stdin.flush()
        return self.p.stdout.readline().split()

    def decide(self, critical=0, warning=0, rssi_weak=0):
        r = self.send(f"D {critical} {warning} {rssi_weak}")
        return int(r[1]), r[2] == "1"

    def observe(self, attempts, failures, rtt, limit=5.0):
        r = self.send(obs(attempts, failures, rtt, limit))
        return {"loss": float(r[0]), "ratio": float(r[1]), "deg": r[2] == "1"}

    def idle(self):
        r = self.send("I")
        return {"loss": float(r[0]), "ratio": float(r[1]), "deg": r[2] == "1"}

    def close(self) -> None:
        self.p.stdin.close()
        self.p.wait(timeout=5)


def simulate(live: Live, cycles: int, loss_of, rng, critical=0, warning=0, rssi_weak=0, max_retries=3, base_rtt=25.0):
    """
    cycles 동안 펌웨어와 같은 순서로 구동합니다: 판정(net_decide_qos) → 전송 시뮬레이션 → 관측/idle.
    loss_of(i): i번째 사이클의 시도당 손실 확률. 반환: 사이클별 기록 리스트.
    """
    log = []
    for i in range(cycles):
        qos, probe = live.decide(critical, warning, rssi_weak)
        p = loss_of(i)
        if qos == 0:
            st = live.idle()
            failed = attempts = 0
        else:
            failed = 0
            while failed <= max_retries and rng.random() < p:      # 시도마다 손실 확률 p로 실패
                failed += 1
            success = failed <= max_retries
            attempts = failed + (1 if success else 0)
            rtt = base_rtt * float(rng.uniform(0.8, 1.6)) if (qos == 1 and success) else 0.0
            st = live.observe(attempts, failed, rtt)
        log.append({"qos": qos, "probe": probe, "deg": st["deg"], "loss": st["loss"], "failed": failed})
    return log


def main() -> int:
    t = Tracker()
    results = []

    def check(name, cond, detail=""):
        results.append(cond)
        print(("PASS" if cond else "FAIL"), name, detail)

    rng = np.random.default_rng(0)

    # 1) 깨끗한 네트워크: RTT 15~35ms, 손실 없음 → 200 트랜잭션 동안 한 번도 혼잡 판정 안 됨
    cmds = [obs(1, 0, float(rng.uniform(15, 35))) for _ in range(200)]
    s = t.run(cmds)
    check("1) 깨끗한 네트워크: 오탐 없음", not any(x["deg"] for x in s),
          f"| 최대 rtt_ratio {max(x['ratio'] for x in s):.2f}, 최대 loss {max(x['loss'] for x in s):.2f}%")

    # 2) 손실 증가: 트랜잭션의 절반이 1회 재전송(시도 2, 실패 1 → 관측 50%) → 평균 손실 25%
    cmds = [obs(1, 0, 25.0) for _ in range(30)]
    first_deg = None
    cmds += [obs(2, 1, 25.0) if i % 2 == 0 else obs(1, 0, 25.0) for i in range(60)]
    s = t.run(cmds)
    for i, x in enumerate(s):
        if i >= 30 and x["deg"]:
            first_deg = i - 30 + 1
            break
    check("2) 손실 증가 → 혼잡 진입", first_deg is not None and first_deg <= 15,
          f"| {first_deg}번째 트랜잭션에서 진입 (손실 상한 5%)")

    # 3) 회복: 손실이 사라진 뒤 해제되되, 곧바로 풀리지 않음(히스테리시스)
    cmds = [obs(2, 1, 25.0) for _ in range(40)] + [obs(1, 0, 25.0) for _ in range(60)]
    s = t.run(cmds)
    deg_at_end_of_loss = s[39]["deg"]
    recover_idx = next((i - 40 + 1 for i in range(40, 100) if not s[i]["deg"]), None)
    # 손실 추정이 지수적으로 감쇠하므로 해제까지 걸리는 시도 수는 대략 ln(손실/해제 임계)/α ≈ 3/α 이내
    check("3) 손실 사라지면 해제 (즉시는 아님)",
          deg_at_end_of_loss and recover_idx is not None and 2 <= recover_idx <= 3.0 / ALPHA + 20,
          f"| 손실 중단 후 {recover_idx}번째 트랜잭션에서 해제 (상한 {3.0 / ALPHA + 20:.0f})")

    # 4) 히스테리시스: 손실이 상한(5%)과 절반(2.5%) 사이로 내려와도 혼잡 상태를 유지하고, 2.5% 아래로 내려가야 해제
    #    (손실은 지수적으로 감쇠하므로 충분히 길게 회복시켜 이 구간을 통과하게 함)
    cmds = [obs(2, 1, 25.0) for _ in range(40)] + [obs(1, 0, 25.0) for _ in range(60)]
    s = t.run(cmds)[40:]
    band = [x for x in s if 2.5 <= x["loss"] < 5.0]
    below = [x for x in s if x["loss"] < 2.5]
    check("4) 히스테리시스: 손실 2.5~5% 구간에서는 혼잡 유지, 2.5% 아래에서 해제",
          len(band) > 0 and all(x["deg"] for x in band) and len(below) > 0 and not below[-1]["deg"],
          f"| 2.5~5% 구간 {len(band)}개 모두 유지, 2.5% 미만 {len(below)}개 중 마지막은 해제")

    # 5) 지연 급증: 손실 없이 RTT가 평소의 5배가 되면 혼잡 진입, 평소로 돌아오면 해제
    cmds = [obs(1, 0, 20.0) for _ in range(20)] + [obs(1, 0, 100.0) for _ in range(20)] + [obs(1, 0, 20.0) for _ in range(30)]
    s = t.run(cmds)
    entered = next((i - 20 + 1 for i in range(20, 40) if s[i]["deg"]), None)
    exited = next((i - 40 + 1 for i in range(40, 70) if not s[i]["deg"]), None)
    check("5) 지연 5배 급증 → 진입 → 회복 시 해제", entered is not None and exited is not None,
          f"| 진입 {entered}번째, 해제 {exited}번째 (손실은 0%, 최대 rtt_ratio {max(x['ratio'] for x in s):.2f})")

    # 6) 예열: RTT 표본이 5개 미만이면 지연 배율은 1.0 (기준이 불안정해 오탐 방지)
    s = t.run([obs(1, 0, 20.0), obs(1, 0, 400.0), obs(1, 0, 400.0), obs(1, 0, 400.0)])
    check("6) RTT 예열 5회 미만은 배율 1.0, 혼잡 아님", all(x["ratio"] == 1.0 and not x["deg"] for x in s))

    # 7) 프로브: ACK 관측 없이 PROBE 사이클이 지나면 프로브 시점, 관측하면 카운터 초기화
    s = t.run(["I"] * PROBE + [obs(1, 0, 20.0)] + ["I"] * PROBE)
    probe_at = next((i + 1 for i, x in enumerate(s) if x["probe"]), None)
    check(f"7) 프로브: QoS 0이 {PROBE}사이클 이어지면 프로브 시점", probe_at == PROBE, f"| {probe_at}번째 사이클")
    check("   관측하면 카운터 초기화", s[PROBE]["idle"] == 0 and not s[PROBE]["probe"])
    check(f"   초기화 후 다시 {PROBE}사이클이 지나야 프로브",
          not any(x["probe"] for x in s[PROBE + 1:2 * PROBE]) and s[2 * PROBE]["probe"])

    # 7b) 적응형 프로브: 성기게 프로브하다가 실패가 관측되면 촘촘히 관측, 안정되면 다시 성기게
    boost = header_const("NET_BOOST_CYCLES", int)
    s = t.run(["I"] * PROBE + [obs(2, 1, 20.0)] + ["I"] * 3 + [obs(1, 0, 20.0)] * (boost + 3) + ["I"] * 3)
    dense = [x["probe"] for x in s[PROBE + 1:PROBE + 1 + 3]]
    check("   적응형 프로브: 실패가 관측되면 바로 다음 사이클도 프로브 시점", all(dense), f"| {dense}")
    tail = s[-3:]
    check("   관측이 정상으로 이어져 촘촘한 관측 기간이 끝나면 다시 성긴 프로브", not any(x["probe"] for x in tail), f"| 부스트 {boost}사이클")

    # 8) 최종 실패(시도 4, 실패 4) → 100% 손실 관측, 반복되면 혼잡
    s = t.run([obs(4, 4, 0.0) for _ in range(5)])
    check("8) 전송 최종 실패 반복 → 혼잡", s[-1]["deg"], f"| 손실 {s[-1]['loss']:.1f}%")

    # 9) 방어: 시도 0, 실패>시도, RTT 0 이하는 안전하게 처리
    s = t.run([obs(0, 0, 0.0), obs(2, 5, 0.0), obs(1, 0, -3.0)])
    check("9) 방어 동작: 시도 0 / 실패>시도 / RTT<=0", all(0.0 <= x["loss"] <= 100.0 for x in s) and s[0]["loss"] == 0.0,
          f"| 손실 {[round(x['loss'], 2) for x in s]}")

    # 10) 손실 상한 설정이 반영됨: 같은 손실이 상한 5%에서는 혼잡, 상한 50%에서는 정상
    cmds5 = [obs(2, 1, 25.0, 5.0) for _ in range(40)]
    cmds50 = [obs(2, 1, 25.0, 50.0) for _ in range(40)]
    check("10) PACKET_LOSS_LIMIT 반영", t.run(cmds5)[-1]["deg"] and not t.run(cmds50)[-1]["deg"],
          "| 상한 5% → 혼잡, 상한 50% → 정상")

    # 11) 리셋
    s = t.run([obs(4, 4, 0.0) for _ in range(5)] + ["R"])
    check("11) 리셋: 모든 상태 초기화", s[-1] == {"loss": 0.0, "ratio": 1.0, "deg": False, "probe": False, "idle": 0})

    # ── 시스템 수준 시뮬레이션: 실제 제품 함수(net_decide_qos, net_observe)가 사이클마다 판정·관측 ─────────
    print("\n[시스템 시뮬레이션] 환경 정상 + 시뮬레이션한 네트워크 (5초 주기, 사이클 = 5초)")

    # S1) 깨끗한 네트워크 + 환경 정상: QoS 0이 기본이고, 프로브 때만 QoS 1
    live = Live(t)
    log = simulate(live, 700, lambda i: 0.0, np.random.default_rng(1)); live.close()
    n1 = sum(1 for x in log if x["qos"] == 1)
    probes = sum(1 for x in log if x["probe"])
    expect = 1.0 / (PROBE + 1)
    check("S1) 깨끗한 네트워크: QoS 1은 프로브뿐, 혼잡 판정 없음",
          n1 == probes and not any(x["deg"] for x in log) and abs(probes / len(log) - expect) < 0.01,
          f"| QoS 0 {100 * (len(log) - n1) / len(log):.1f}%, 프로브 {probes}회 ({100 * probes / len(log):.1f}%: {PROBE}사이클마다 1회, 기대 {100 * expect:.1f}%)")

    # S2) 손실 구간: 100~300 사이클에 시도당 손실 40%, 나머지 깨끗
    live = Live(t)
    log = simulate(live, 500, lambda i: 0.40 if 100 <= i < 300 else 0.0, np.random.default_rng(2)); live.close()
    onset = next((i - 100 for i in range(100, 300) if log[i]["deg"]), None)
    during = log[onset + 100 + 1:300] if onset is not None else []
    recover = next((i - 300 for i in range(300, 500) if not log[i]["deg"]), None)
    check("S2) 손실 발생 → 감지 → 혼잡 중엔 매 사이클 QoS 1 이상 → 회복 시 QoS 0 복귀",
          onset is not None and onset <= 40
          and len(during) > 0 and all(x["qos"] >= 1 for x in during)
          and recover is not None and recover <= 60
          and sum(1 for x in log[recover + 300 + 10:] if x["qos"] == 0) > 0,      # 해제 후에는 다시 QoS 0을 쓴다
          f"| 손실 시작 후 {onset}사이클({(onset or 0) * 5}초)에 감지, 손실 중단 후 {recover}사이클({(recover or 0) * 5}초)에 해제")

    # S3) 환경 위험: 네트워크와 무관하게 항상 QoS 2 (TCP)
    live = Live(t)
    log = simulate(live, 100, lambda i: 0.3, np.random.default_rng(3), critical=1); live.close()
    check("S3) 환경 위험(critical)이면 항상 QoS 2, 프로브 없음", all(x["qos"] == 2 and not x["probe"] for x in log))

    # S4) RSSI 약함이면 항상 QoS 1 이상
    live = Live(t)
    log = simulate(live, 100, lambda i: 0.0, np.random.default_rng(4), rssi_weak=1); live.close()
    check("S4) RSSI 약함이면 QoS 1 (프로브 아님)", all(x["qos"] == 1 and not x["probe"] for x in log))

    # 손실 수준별 통계는 시드 하나에 의존하면 우연에 좌우되므로 여러 시드의 분포로 판정한다
    # (탐색 때 시드 3개로 잡은 평균이 낙관적이었음: 손실 3%에서 실제 평균은 전이 10회, 혼잡 시간 5%).
    def steady_stats(p, seeds=range(12), cycles=3000):
        flips, frac = [], []
        for sd in seeds:
            lv = Live(t)
            lg = simulate(lv, cycles, lambda i: p, np.random.default_rng(100 + sd)); lv.close()
            flips.append(sum(1 for a, b in zip(lg, lg[1:]) if a["deg"] != b["deg"]))
            frac.append(100 * sum(1 for x in lg if x["deg"]) / len(lg))
        return flips, frac

    # S5) 손실이 낮아(1%) 상한(5%)에서 멀면 거의 조용하다
    flips, frac = steady_stats(0.01)
    check("S5) 손실 1%(상한의 1/5): 혼잡 오탐이 드묾", np.median(flips) <= 2 and np.mean(frac) <= 2.0,
          f"| 3000사이클(약 4시간) 전이 중앙값 {np.median(flips):.0f}회(최대 {max(flips)}), 혼잡 시간 평균 {np.mean(frac):.1f}%")

    # S6) 손실 3%(상한의 60%): 통계적으로 상한과 구분하기 어려워 가끔 혼잡으로 오판하지만, 빠르게 깜빡이진 않는다
    flips, frac = steady_stats(0.03)
    check("S6) 손실 3%(상한의 60%): 가끔 오판하되 빠르게 깜빡이지 않음",
          np.median(flips) <= 16 and max(flips) <= 40 and np.mean(frac) <= 12.0,
          f"| 전이 중앙값 {np.median(flips):.0f}회(최대 {max(flips)}), 혼잡 오판 시간 평균 {np.mean(frac):.1f}% (최대 {max(frac):.1f}%)")

    # S7) 손실 10%(상한의 2배): 혼잡을 놓치지 않고 대체로 혼잡 상태로 판정한다
    flips, frac = steady_stats(0.10)
    check("S7) 손실 10%(상한의 2배): 혼잡 상태로 판정하는 시간이 절반 이상", np.mean(frac) >= 50.0,
          f"| 혼잡 판정 시간 평균 {np.mean(frac):.1f}% (시드별 최소 {min(frac):.1f}%, 최대 {max(frac):.1f}%)")
    print(f"      ※ 상한의 2배 손실도 약 {100 - np.mean(frac):.0f}%의 시간은 감지하지 못합니다 — 성긴 프로브의 한계 (아래 '알려진 한계')")

    # S8) 프로브 비용: 프로브가 QoS 0의 절전 효과를 얼마나 깎는가 (전력 모델 사용)
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "backend"))
        import logging
        logging.disable(logging.CRITICAL)
        from app.services.power_estimator import estimate_cycle_energy
        e0 = estimate_cycle_energy(0, 1.0, 0, 100.0, 5000.0)["total_energy_mwh"]     # QoS 0 사이클
        e1 = estimate_cycle_energy(1, 25.0, 0, 130.0, 5000.0)["total_energy_mwh"]    # QoS 1 사이클 (RTT 25ms)
        per7 = (PROBE * e0 + e1) / (PROBE + 1)
        print(f"\n[프로브 비용] {PROBE}사이클마다 QoS 1 프로브 1회 → 평균 사이클 에너지 QoS 0만일 때 대비 +{100 * (per7 / e0 - 1):.1f}% "
              f"(QoS 0 {e0 * 1000:.3f} μWh, QoS 1 {e1 * 1000:.3f} μWh). ※ 전력 모델의 가정값 기준이며 실측이 아님")
    except Exception as exc:                        # 전력 모델을 못 불러와도 시험 자체는 계속
        print(f"\n[프로브 비용] 계산 생략 ({exc})")

    print("\n결과:", "모두 통과" if all(results) else f"실패 {results.count(False)}건")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
