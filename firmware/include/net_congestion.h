#pragma once
/*
 * firmware/include/net_congestion.h
 * ─────────────────────────────────────────────────────────────────────────────
 * 네트워크 혼잡도 추적기. 표준 C++만 사용하므로 PC에서도 컴파일됩니다
 * (firmware/test/test_net_congestion.py가 g++로 시나리오별 동작을 검증합니다).
 *
 * 왜 필요한가
 *   RSSI는 신호 세기일 뿐 혼잡도가 아닙니다(신호가 강해도 혼잡해서 손실될 수 있음). 혼잡은 실제 전송의
 *   결과(손실, 지연)로만 알 수 있으므로, ACK를 받는 전송의 결과를 추적합니다.
 *
 * 지표
 *   loss_pct   : 전송 "시도" 하나하나의 실패 여부(실패 100 / 성공 0)를 반영한 지수이동평균(%).
 *                트랜잭션마다 (실패/시도) 비율을 평균하면 손실이 실제보다 낮게 추정되어(실패한 트랜잭션은 시도가
 *                2번이라 50%로 잡힘) 시도 단위로 반영합니다. 손실 10%일 때 트랜잭션 평균 방식은 혼잡 판정을
 *                31%의 시간만 했지만 시도 단위 방식은 훨씬 정확합니다.
 *   rtt_ratio  : 최근 RTT(지수이동평균) / 평소 최저 RTT. 1이면 평소와 같고 클수록 지연이 늘어난 것.
 *                기준값은 "가장 낮은 RTT"를 추적하고 아주 느리게 위로 적응합니다(gas_baseline.h의 반대 방향).
 *                UDP QoS 1의 마지막 시도 RTT만 사용합니다 (재전송 타임아웃 대기 시간이 섞이지 않게 하고,
 *                TCP 연결 설정 시간이 섞이지 않게 함). TCP 전송은 손실만 반영합니다.
 *
 * 혼잡 판정 (히스테리시스: 진입과 해제 조건을 달리해 경계에서 깜빡이지 않게 함)
 *   진입: loss_pct > 손실 상한(PACKET_LOSS_LIMIT)  또는  rtt_ratio > NET_RTT_ENTER
 *   해제: loss_pct < 손실 상한 / 2               그리고  rtt_ratio < NET_RTT_EXIT
 *
 * QoS 0의 관측 문제
 *   QoS 0은 ACK가 없어 손실도 RTT도 볼 수 없습니다. 그래서 ACK 전송 없이 NET_PROBE_INTERVAL 사이클이 지나면
 *   한 번 QoS 1로 보내 측정합니다 ("프로브"). 혼잡 상태이거나 QoS 1 이상이면 매 사이클 관측하므로 프로브가 필요 없습니다.
 *   적응형 프로브: 프로브에서 실패가 관측되거나 혼잡이 해제되면 NET_BOOST_CYCLES 동안 촘촘히 관측합니다.
 *   평소에는 성기게(비용 절약), 문제가 의심될 때만 빽빽하게 관측하는 방식입니다.
 *
 * 알려진 한계
 *   · 중간 수준의 손실(예: 상한의 2배)은 성긴 프로브로는 감지가 느립니다(시뮬레이션에서 평균 약 30분).
 *     프로브가 실패를 우연히 만나야 하기 때문이며, 정보량과 에너지의 교환 관계입니다.
 *   · 손실이 상한 근처(예: 3~5%)이면 통계적으로 구분하기 어려워 혼잡 상태가 가끔 오갑니다.
 *
 * ★ 아래 상수는 모두 가정값입니다. 실제 네트워크(손실·지연을 인위적으로 만든 실험)에서 보정해야 합니다.
 * ─────────────────────────────────────────────────────────────────────────────
 */

#include <stdint.h>

// 기본값은 시뮬레이션(손실 1/3/10/20/40%, 5초 주기)으로 정했습니다. 근거와 교환 관계는 아래 표와 같습니다.
//   프로브 간격  깨끗한 망의 QoS 1 비중   손실 40%/20%/10% 감지 지연(평균 사이클)
//        12            7.7%                      21 / 66 / 222
//        24            4.0%  ← 기본            59 / 116 / 334
//   오탐/재현율(기본값, 시드 12개 평균): 실제 손실 1%면 혼잡 오판이 거의 없고(혼잡 시간 0%), 3%(상한의 60%)면
//   약 4%의 시간을 혼잡으로 오판하며(전이 중앙값 4회/4시간), 10%(상한의 2배)면 약 86%의 시간을 혼잡으로 판정합니다.
//   ※ 이 수치는 "시도마다 독립적으로 손실"이라는 단순한 시뮬레이션입니다. 실제 무선 손실은 연달아 생기는 경향이 있어
//     결과가 다를 수 있습니다.
//   간격을 줄이면 감지는 빨라지지만 QoS 0의 절전 효과를 더 깎습니다. 실제 네트워크에서 보정하세요.
#ifndef NET_LOSS_ALPHA
#define NET_LOSS_ALPHA 0.05f   // 손실률 이동평균 계수(시도당). 유효 창 약 20번의 시도
#endif
#ifndef NET_BOOST_CYCLES
#define NET_BOOST_CYCLES 32   // 실패가 관측되거나 혼잡이 해제되면 이만큼 사이클 동안 QoS 1로 촘촘히 관측 (0이면 끔)
#endif
#ifndef NET_RTT_ALPHA
#define NET_RTT_ALPHA 0.2f   // RTT 이동평균 계수
#endif
#ifndef NET_RTT_BASE_RISE
#define NET_RTT_BASE_RISE 0.002f   // 기준(최저) RTT가 샘플당 위로 적응하는 비율 (네트워크 환경 변화 추종)
#endif
#ifndef NET_RTT_WARMUP
#define NET_RTT_WARMUP 5   // 이 횟수만큼 RTT를 모으기 전에는 rtt_ratio = 1.0 (기준이 불안정)
#endif
#ifndef NET_RTT_ENTER
#define NET_RTT_ENTER 3.0f   // rtt_ratio가 이를 넘으면 혼잡 진입
#endif
#ifndef NET_RTT_EXIT
#define NET_RTT_EXIT 2.0f   // rtt_ratio가 이 아래로 내려가야 해제
#endif
#ifndef NET_PROBE_INTERVAL
#define NET_PROBE_INTERVAL 24   // ACK 관측 없이 이만큼 사이클이 지나면 QoS 1 프로브 (5초 주기 기준 2분)
#endif

struct NetCongestion {
  float    loss_pct;    // 손실률 EWMA (%) — 전송 "시도" 하나하나를 반영 (트랜잭션 평균이 아님)
  float    rtt_ewma;    // RTT EWMA (ms)
  float    rtt_base;    // 평소 최저 RTT (ms)
  uint32_t rtt_n;       // 반영한 RTT 샘플 수
  bool     degraded;    // 혼잡 상태 (히스테리시스 적용)
  uint32_t idle_cycles; // ACK 기반 관측 없이 지난 연속 사이클 수 (프로브 판단용)
  uint32_t boost;       // 실패가 관측된 뒤 촘촘히 관측할 남은 사이클 수 (적응형 프로브)
};

static inline void net_congestion_reset(NetCongestion &n) {
  n.loss_pct = 0.0f;
  n.rtt_ewma = 0.0f;
  n.rtt_base = 0.0f;
  n.rtt_n = 0;
  n.degraded = false;
  n.idle_cycles = 0;
  n.boost = 0;
}

// 평소 대비 지연 배율 (1.0 = 평소와 같음). 표본이 부족하면 1.0.
static inline float net_rtt_ratio(const NetCongestion &n) {
  if (n.rtt_n < NET_RTT_WARMUP || !(n.rtt_base > 0.0f)) {
    return 1.0f;
  }
  return n.rtt_ewma / n.rtt_base;
}

// 이번 사이클에 QoS 1 프로브를 보내야 하는가.
//   · ACK 관측 없이 NET_PROBE_INTERVAL 사이클이 지났거나 (평소의 성긴 프로브)
//   · 최근 관측에서 실패가 있어 촘촘히 관측 중이면 (적응형 프로브: 문제가 의심될 때만 비용을 씀)
static inline bool net_probe_due(const NetCongestion &n) {
  return n.idle_cycles >= NET_PROBE_INTERVAL || n.boost > 0;
}

// 환경 위험 QoS와 네트워크 상태를 합친 최종 QoS 결정. 펌웨어(main_gingerbread.cpp)와 시험이 같은 코드를 씁니다.
//   QoS = max(환경 위험 QoS, 네트워크 QoS).  네트워크(RSSI 약함 / 혼잡 / 프로브)는 QoS를 최대 1까지만 올립니다.
//   QoS 2(TCP)는 환경 위험 전용입니다. 혼잡한 네트워크에서 TCP로 바꾸면 오히려 혼잡을 키울 수 있기 때문입니다.
//   프로브: 그렇지 않았다면 QoS 0이었을 사이클(환경 정상, RSSI 좋음, 혼잡 아님)에서 관측이 오래 없었다면 QoS 1로 보냅니다.
struct QosDecision {
  int  qos;     // 0, 1, 2
  bool probe;   // 이 사이클이 관측용 프로브인가 (probe가 true면 qos는 1)
};

static inline QosDecision net_decide_qos(bool env_critical, bool env_warning, bool rssi_weak,
                                         const NetCongestion &n) {
  QosDecision d;
  d.probe = false;
  if (env_critical) {
    d.qos = 2;
    return d;
  }
  const bool would_be_qos0 = !env_warning && !rssi_weak && !n.degraded;
  d.probe = would_be_qos0 && net_probe_due(n);
  d.qos = (env_warning || rssi_weak || n.degraded || d.probe) ? 1 : 0;
  return d;
}

// ACK 없는 사이클(QoS 0)이 지났음을 알립니다.
static inline void net_on_idle_cycle(NetCongestion &n) {
  if (n.idle_cycles < 0xFFFFFFFFu) {
    n.idle_cycles++;
  }
}

// ACK 기반 전송(QoS 1, TCP) 하나의 결과를 반영합니다.
//   attempts   : 전체 전송 시도 수 (성공 시 재전송 횟수 + 1, 최종 실패 시 재전송 횟수)
//   failures   : 실패한 시도 수 (= 재전송 횟수)
//   rtt_ms     : 마지막(성공한) 시도의 RTT. 알 수 없으면(TCP, QoS 2 UDP, 실패) 0 이하를 넘깁니다.
//   loss_limit : 손실 상한 (%, PACKET_LOSS_LIMIT). 혼잡 판정 임계값.
static inline void net_observe(NetCongestion &n, uint32_t attempts, uint32_t failures,
                               float rtt_ms, float loss_limit) {
  n.idle_cycles = 0;

  if (attempts > 0) {
    // 전송 "시도" 하나하나를 순서대로 반영합니다: 실패한 시도(100) 여러 번, 이어서 성공한 시도(0) 한 번.
    // 트랜잭션마다 (실패/시도) 비율의 평균을 내면 손실이 실제보다 낮게 추정됩니다
    // (실패한 트랜잭션은 시도가 2번이라 비율이 50%로 잡히고 성공한 트랜잭션은 0%라 평균이 왜곡됨).
    // 시도 단위로 반영하면 손실 확률 p의 불편 추정량이 됩니다.
    const uint32_t f = (failures > attempts) ? attempts : failures;   // 방어: 실패 ≤ 시도
    for (uint32_t i = 0; i < f; i++) {
      n.loss_pct = NET_LOSS_ALPHA * 100.0f + (1.0f - NET_LOSS_ALPHA) * n.loss_pct;
    }
    if (attempts > f) {
      n.loss_pct = (1.0f - NET_LOSS_ALPHA) * n.loss_pct;              // 성공한 시도
    }
    // 실패가 관측되면 한동안 촘촘히 관측해 진짜 혼잡인지 빠르게 확인합니다 (평소에는 프로브를 성기게 둘 수 있음).
    if (f > 0) {
      n.boost = NET_BOOST_CYCLES;
    } else if (n.boost > 0) {
      n.boost--;
    }
  }

  if (rtt_ms > 0.0f) {
    if (n.rtt_n == 0) {
      n.rtt_ewma = rtt_ms;
      n.rtt_base = rtt_ms;
    } else {
      n.rtt_ewma = NET_RTT_ALPHA * rtt_ms + (1.0f - NET_RTT_ALPHA) * n.rtt_ewma;
      const float risen = n.rtt_base * (1.0f + NET_RTT_BASE_RISE);   // 기준은 느리게 위로만 적응
      n.rtt_base = (rtt_ms < risen) ? rtt_ms : risen;
    }
    if (n.rtt_n < 0xFFFFFFFFu) {
      n.rtt_n++;
    }
  }

  const float ratio = net_rtt_ratio(n);
  if (!n.degraded) {
    if (n.loss_pct > loss_limit || ratio > NET_RTT_ENTER) {
      n.degraded = true;
    }
  } else {
    if (n.loss_pct < loss_limit * 0.5f && ratio < NET_RTT_EXIT) {
      n.degraded = false;
      // 해제 직후에도 잠시 촘촘히 관측합니다. 손실이 중간 수준이면 잡음 때문에 추정치가 잠깐 낮아져 해제될 수 있고,
      // 그때 성긴 프로브로만 다시 감지하면 오래 걸립니다. 재발하면 실패 한 번으로 바로 다시 진입합니다.
      n.boost = NET_BOOST_CYCLES;
    }
  }
}
