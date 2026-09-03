import type { ControlCommand, ControlResult, TelemetrySource } from '@/data/source';
import type { ConfigDto, HandshakeDto, LatestTelemetryDto, SessionStatsDto, StatsSummaryDto, TelemetryLogDto } from '@/types/api';
import type { ExperimentQuery, ExperimentSummary, OperationsSnapshot, ProtocolSnapshot } from '@/types/domain';
import { apiGet, apiPost } from './client';
import {
  toAiDecision,
  toComparison,
  toConfigEntries,
  toHandshake,
  toKpis,
  toNetworkQuality,
  toNodeStatus,
  toPacketLog,
  toQosDistribution,
} from './mappers';

const OHM_PER_KOHM = 1000;

/** 아직 없는 엔드포인트는 실패해도 화면 전체를 막지 않도록 null로 흡수합니다. */
async function optional<T>(p: Promise<T>): Promise<T | null> {
  try {
    return await p;
  } catch {
    return null;
  }
}

export function createHttpSource(): TelemetrySource {
  return {
    kind: 'http',

    async getOperationsSnapshot(signal): Promise<OperationsSnapshot> {
      const [latest, config, logs, stats, sessions] = await Promise.all([
        apiGet<LatestTelemetryDto>('/api/v1/telemetry/latest', signal),
        optional(apiGet<ConfigDto>('/api/config', signal)),
        optional(apiGet<TelemetryLogDto>('/api/v1/logs/telemetry?limit=60', signal)),
        optional(apiGet<StatsSummaryDto>('/api/v1/stats/summary', signal)),
        optional(apiGet<SessionStatsDto>('/api/sessions/stats', signal)),
      ]);
      void sessions;

      const points = (logs?.data ?? []).slice(-24);

      return {
        node: toNodeStatus(latest),
        kpis: toKpis(latest, logs, stats),
        environment: [
          {
            key: 'gas',
            label: '가스 저항',
            unit: 'kΩ',
            precision: 1,
            axis: { min: 0, max: 40 },
            threshold: config
              ? { value: config.ENVIRONMENT.GAS_THRESHOLD_KOHM, label: `임계 ${config.ENVIRONMENT.GAS_THRESHOLD_KOHM.toFixed(1)} 이하`, direction: 'min' }
              : undefined,
            points: points.map((r) => ({ t: r.timestamp, value: (r.gas ?? 0) / OHM_PER_KOHM })),
          },
          {
            key: 'temp',
            label: '온도',
            unit: '°C',
            precision: 1,
            axis: { min: 0, max: 50 },
            threshold: config
              ? { value: config.ENVIRONMENT.TEMP_THRESHOLD_CELSIUS, label: `임계 ${config.ENVIRONMENT.TEMP_THRESHOLD_CELSIUS.toFixed(1)} 이상`, direction: 'max' }
              : undefined,
            points: points.map((r) => ({ t: r.timestamp, value: r.temp ?? 0 })),
          },
        ],
        networkQuality: toNetworkQuality(latest, stats, config),
        powerTrend: {
          unit: 'mWh',
          points: (stats?.systems.gingerbread.energy_series ?? []).map((p, i) => ({
            minute: p.minute,
            gingerbread: p.value,
            legacy: stats?.systems.legacy.energy_series?.[i]?.value ?? 0,
          })),
        },
        aiDecision: toAiDecision(latest),
        config: config ? toConfigEntries(config) : [],
        packetLog: logs ? toPacketLog(logs) : [],
      };
    },

    async getExperimentSummary(query: ExperimentQuery, signal): Promise<ExperimentSummary> {
      const params = new URLSearchParams();
      if (query.qos !== undefined && query.qos !== 'all') params.set('qos', String(query.qos));
      if (query.rangeMinutes) params.set('range_minutes', String(query.rangeMinutes));
      const stats = await apiGet<StatsSummaryDto>(`/api/v1/stats/summary?${params.toString()}`, signal);

      const g = stats.systems.gingerbread;
      const l = stats.systems.legacy;
      const ctx = stats.window.sample_count ? `n = ${stats.window.sample_count.toLocaleString('ko-KR')}` : undefined;

      const headline = [
        toComparison('energy', '누적 소비 전력', 'mWh', 1, 'lower-is-better', g.cumulative_energy_mwh, l.cumulative_energy_mwh, ctx),
        toComparison('packetLoss', '패킷 손실', '%', 1, 'lower-is-better', g.packet_loss_pct, l.packet_loss_pct, ctx),
        toComparison('successRate', '전송 성공률', '%', 1, 'higher-is-better', g.success_rate_pct, l.success_rate_pct, ctx),
        toComparison('latency', '평균 지연', 'ms', 1, 'lower-is-better', g.avg_latency_ms, l.avg_latency_ms, ctx),
      ];

      return {
        headline,
        successRate: headline[2],
        energy: headline[0],
        qosDistribution: toQosDistribution(stats),
        latencyTrend: (g.latency_series ?? []).map((p, i) => ({
          minute: p.minute,
          gingerbread: p.value,
          legacy: l.latency_series?.[i]?.value ?? 0,
        })),
        summaryText: '',
      };
    },

    async getProtocolSnapshot(signal): Promise<ProtocolSnapshot> {
      const [sessions, flows] = await Promise.all([
        optional(apiGet<SessionStatsDto>('/api/sessions/stats', signal)),
        Promise.all(
          [0, 1, 2].map((q) => optional(apiGet<HandshakeDto>(`/api/v1/protocol/handshake?qos=${q}`, signal))),
        ),
      ]);

      return {
        session: {
          activeSessions: sessions?.active ?? 0,
          totalSessions: sessions?.total ?? 0,
          gatewayOnline: true,
          gatewayPort: 5000,
          gatewayTransport: 'UDP',
          keepAliveSeconds: null,
        },
        flows: flows.filter((f): f is HandshakeDto => f !== null).map(toHandshake),
        packetMetadata: [],
        timeline: [],
      };
    },

    async sendControl(input: ControlCommand): Promise<ControlResult> {
      const res = await apiPost<{ status: string; message: string }>('/api/v1/control', {
        device_ip: input.deviceIp,
        device_port: input.devicePort,
        qos_level: input.qosLevel,
        sleep_interval: input.sleepIntervalMs,
      });
      return { ok: res.status === 'ok', message: res.message };
    },
  };
}
