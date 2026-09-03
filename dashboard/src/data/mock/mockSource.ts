import type { ControlCommand, ControlResult, TelemetrySource } from '@/data/source';
import type { ExperimentQuery, ExperimentSummary, OperationsSnapshot, ProtocolSnapshot } from '@/types/domain';
import { experimentSummary, operationsSnapshot, protocolSnapshot } from './fixtures';

const delay = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** 값을 살짝 흔들어 "살아 있는 화면"처럼 보이게 합니다. 정합성은 유지합니다. */
function jitter(value: number, amount: number, precision: number): number {
  const next = value + (Math.random() - 0.5) * amount * 2;
  const f = 10 ** precision;
  return Math.round(next * f) / f;
}

function animate(snapshot: OperationsSnapshot): OperationsSnapshot {
  const kpis = snapshot.kpis.map((kpi) => {
    if (kpi.key === 'rssi') {
      const value = jitter(kpi.value, 1.5, 0);
      return { ...kpi, value, history: [...kpi.history.slice(1), value] };
    }
    if (kpi.key === 'successRate') {
      const value = Math.min(99.9, jitter(kpi.value, 0.15, 1));
      return { ...kpi, value, history: [...kpi.history.slice(1), value] };
    }
    return kpi;
  });
  return { ...snapshot, kpis, node: { ...snapshot.node, updatedAt: new Date().toISOString() } };
}

export function createMockSource(): TelemetrySource {
  let current = operationsSnapshot;

  return {
    kind: 'mock',

    async getOperationsSnapshot() {
      await delay(180);
      current = animate(current);
      return current;
    },

    async getExperimentSummary(query: ExperimentQuery): Promise<ExperimentSummary> {
      await delay(180);
      if (!query.qos || query.qos === 'all' || query.qos === 'dynamic') return experimentSummary;
      // 단일 QoS 필터를 고르면 해당 레벨만 남긴 분포를 보여줍니다.
      return {
        ...experimentSummary,
        qosDistribution: experimentSummary.qosDistribution.map((row) => {
          const kept = row.segments.filter((s) => s.qos === query.qos);
          return kept.length ? { ...row, segments: [{ ...kept[0], share: 100 }] } : row;
        }),
      };
    },

    async getProtocolSnapshot(): Promise<ProtocolSnapshot> {
      await delay(120);
      return protocolSnapshot;
    },

    async sendControl(input: ControlCommand): Promise<ControlResult> {
      await delay(240);
      return {
        ok: true,
        message: `[mock] QoS ${input.qosLevel} · sleep ${input.sleepIntervalMs} ms 명령을 ${input.deviceIp}:${input.devicePort} 로 보낸 것으로 처리했습니다.`,
      };
    },

    subscribe(onSnapshot) {
      const id = window.setInterval(() => {
        current = animate(current);
        onSnapshot(current);
      }, 3000);
      return () => window.clearInterval(id);
    },
  };
}
