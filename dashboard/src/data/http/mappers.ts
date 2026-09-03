/* ---------------------------------------------------------------------------
 * 백엔드 DTO → 도메인 모델.
 * 백엔드 스키마가 바뀌면 고쳐야 하는 곳은 여기 하나입니다.
 * ------------------------------------------------------------------------- */
import type {
  ConfigDto,
  HandshakeDto,
  LatestTelemetryDto,
  StatsSummaryDto,
  TelemetryLogDto,
} from '@/types/api';
import type {
  AiDecision,
  ComparisonMetric,
  ConfigEntry,
  GaugeMetric,
  HandshakeFlow,
  KpiMetric,
  NodeStatus,
  PacketLogRow,
  QosDistributionRow,
  QosLevel,
  StatusLevel,
} from '@/types/domain';

const OHM_PER_KOHM = 1000;

export const toQos = (v: number | undefined): QosLevel => (v === 2 ? 2 : v === 1 ? 1 : 0);

export const networkStatus = (flag: number | undefined): { level: StatusLevel; label: string } =>
  flag === 1 ? { level: 'serious', label: '불안정' } : { level: 'normal', label: '양호' };

export const urgencyStatus = (flag: number | undefined): { level: StatusLevel; label: string } =>
  flag === 1 ? { level: 'critical', label: '긴급' } : { level: 'normal', label: '정상' };

function firstPower(dto: LatestTelemetryDto) {
  const entries = Object.values(dto.power ?? {});
  return entries.length ? entries[0] : undefined;
}

export function toNodeStatus(dto: LatestTelemetryDto, sessionUptimeSeconds = 0): NodeStatus {
  const env = dto.environment;
  return {
    nodeName: 'NODE 1',
    hardware: 'ESP32-S3',
    ip: '—',
    sessionId: env.client_id ?? '—',
    uptimeSeconds: sessionUptimeSeconds,
    network: networkStatus(env.network_status),
    urgency: {
      ...urgencyStatus(env.data_urgency),
      detail: env.data_urgency === 1 ? '가스 농도 임계 초과' : undefined,
    },
    currentQos: toQos(env.qos),
    gatewayConnected: true,
    updatedAt: dto.server_time,
  };
}

export function toKpis(dto: LatestTelemetryDto, history: TelemetryLogDto | null, stats: StatsSummaryDto | null): KpiMetric[] {
  const env = dto.environment;
  const power = firstPower(dto);
  const g = stats?.systems.gingerbread;
  const legacy = stats?.systems.legacy;

  const rssiHistory = (history?.data ?? [])
    .map((r) => (r as { rssi?: number }).rssi)
    .filter((v): v is number => typeof v === 'number')
    .slice(-8);

  return [
    {
      key: 'rssi',
      label: 'RSSI',
      value: env.rssi ?? 0,
      unit: 'dBm',
      precision: 0,
      history: rssiHistory,
      sparkKind: 'line',
      status: (env.rssi ?? 0) < -80 ? 'serious' : 'normal',
      threshold: { value: -80, label: '임계 −80 dBm 미달', direction: 'min' },
    },
    {
      key: 'successRate',
      label: '전송 성공률',
      value: g?.success_rate_pct ?? 0,
      unit: '%',
      precision: 1,
      history: [],
      sparkKind: 'line',
      status: 'normal',
      baseline: legacy
        ? { value: legacy.success_rate_pct, label: `베이스라인 ${legacy.success_rate_pct.toFixed(1)}% 대비`, trend: 'higher-is-better' }
        : undefined,
    },
    {
      key: 'energy',
      label: '누적 소비 전력',
      value: g?.cumulative_energy_mwh ?? power?.estimated_energy_mwh ?? 0,
      unit: 'mWh',
      precision: 1,
      history: (g?.energy_series ?? []).map((p) => p.value),
      sparkKind: 'line',
      status: 'normal',
      baseline: legacy
        ? { value: legacy.cumulative_energy_mwh, label: `베이스라인 ${legacy.cumulative_energy_mwh.toFixed(1)} mWh 대비`, trend: 'lower-is-better' }
        : undefined,
    },
    {
      key: 'battery',
      label: '배터리',
      value: env.battery ?? 0,
      unit: '%',
      precision: 0,
      history: [env.battery ?? 0],
      sparkKind: 'bar',
      status: 'normal',
      note: '가상 배터리 값',
    },
  ];
}

export function toNetworkQuality(dto: LatestTelemetryDto, stats: StatsSummaryDto | null, config: ConfigDto | null): GaugeMetric[] {
  const power = firstPower(dto);
  const g = stats?.systems.gingerbread;
  const rssiThreshold = config?.NETWORK.RSSI_THRESHOLD ?? -80;
  const lossLimit = config?.NETWORK.PACKET_LOSS_LIMIT ?? 5;

  return [
    {
      key: 'latency',
      label: '지연 시간',
      labelEn: 'Latency',
      value: g?.avg_latency_ms ?? power?.rtt_ms ?? 0,
      unit: 'ms',
      precision: 0,
      scale: { min: 0, max: 150 },
      threshold: { value: 100, label: '임계 100 ms', direction: 'max' },
      status: 'normal',
    },
    {
      key: 'packetLoss',
      label: '패킷 손실',
      labelEn: 'Packet Loss',
      value: g?.packet_loss_pct ?? 0,
      unit: '%',
      precision: 1,
      scale: { min: 0, max: 10 },
      threshold: { value: lossLimit, label: `허용 한계 ${lossLimit} %`, direction: 'max' },
      status: (g?.packet_loss_pct ?? 0) > lossLimit ? 'serious' : 'normal',
    },
    {
      key: 'rssi',
      label: '신호 강도',
      labelEn: 'RSSI',
      value: dto.environment.rssi ?? 0,
      unit: 'dBm',
      precision: 0,
      scale: { min: -100, max: -40 },
      threshold: { value: rssiThreshold, label: `임계 ${rssiThreshold} dBm`, direction: 'min' },
      status: (dto.environment.rssi ?? 0) < rssiThreshold ? 'serious' : 'normal',
    },
    {
      key: 'retry',
      label: '재전송',
      labelEn: 'Retry',
      value: g?.retry_per_100 ?? power?.retry_count ?? 0,
      unit: '회 / 100 패킷',
      precision: 0,
      scale: { min: 0, max: 20 },
      threshold: { value: 10, label: '임계 10회', direction: 'max' },
      status: 'normal',
    },
  ];
}

export function toAiDecision(dto: LatestTelemetryDto): AiDecision {
  const env = dto.environment;
  const score = env.nn_score ?? 0;
  const selectedQos: QosLevel = score >= 0.75 ? 2 : score >= 0.4 ? 1 : 0;
  const upgraded = env.network_status === 1 && selectedQos < 2;
  return {
    inputs: [
      { label: '가스 저항', value: (env.gas ?? 0) / OHM_PER_KOHM, unit: 'kΩ', precision: 1 },
      { label: '온도', value: env.temp ?? 0, unit: '°C', precision: 1 },
      { label: 'RSSI', value: env.rssi ?? 0, unit: 'dBm', precision: 0 },
      { label: '배터리', value: env.battery ?? 0, unit: '%', precision: 0 },
    ],
    score,
    bands: { qos1: 0.4, qos2: 0.75 },
    selectedQos: upgraded ? ((selectedQos + 1) as QosLevel) : selectedQos,
    reason: upgraded ? '네트워크 불안정으로 한 단계 상향' : '추론 점수 구간에 따른 선택',
  };
}

export function toConfigEntries(dto: ConfigDto): ConfigEntry[] {
  return [
    { key: 'powerMode', label: '전원 모드', value: dto.POWER_MANAGEMENT.POWER_MODE },
    { key: 'rssiThreshold', label: 'RSSI 임계', value: `${dto.NETWORK.RSSI_THRESHOLD} dBm` },
    { key: 'gasThreshold', label: '가스 임계', value: `${dto.ENVIRONMENT.GAS_THRESHOLD_KOHM.toFixed(1)} kΩ` },
    { key: 'tempThreshold', label: '온도 임계', value: `${dto.ENVIRONMENT.TEMP_THRESHOLD_CELSIUS.toFixed(1)} °C` },
    { key: 'battery', label: '가상 배터리', value: `${dto.POWER_MANAGEMENT.CURRENT_BATTERY_LEVEL} %` },
  ];
}

export function toPacketLog(dto: TelemetryLogDto): PacketLogRow[] {
  return dto.data.map((row, i) => ({
    id: `${row.timestamp}-${row.msg_id}-${i}`,
    time: row.timestamp,
    msgId: `0x${row.msg_id.toString(16).toUpperCase().padStart(4, '0')}`,
    type: row.msg_type ?? 'PUBLISH',
    qos: toQos(row.qos),
    urgency: urgencyStatus(row.data_urgency),
    network: networkStatus(row.network_status),
    result:
      row.delivered === false
        ? { level: 'critical' as StatusLevel, label: '유실' }
        : { level: 'normal' as StatusLevel, label: '전달' },
    retry: row.retry_count ?? 0,
    rttMs: row.rtt_ms ?? null,
  }));
}

export function toComparison(
  key: string,
  label: string,
  unit: string,
  precision: number,
  trend: ComparisonMetric['trend'],
  g: number,
  legacy: number,
  context?: string,
): ComparisonMetric {
  return { key, label, unit, precision, gingerbread: g, legacy, trend, context };
}

export function toQosDistribution(stats: StatsSummaryDto): QosDistributionRow[] {
  const build = (system: 'gingerbread' | 'legacy', label: string): QosDistributionRow => ({
    system,
    label,
    segments: stats.systems[system].qos_distribution.map((d) => ({ qos: toQos(d.qos), share: d.share_pct })),
  });
  return [build('gingerbread', 'Gingerbread'), build('legacy', 'Legacy MQTT')];
}

export function toHandshake(dto: HandshakeDto): HandshakeFlow {
  return {
    qos: toQos(dto.qos),
    msgId: dto.msg_id,
    messages: dto.messages.map((m) => ({ name: m.name, direction: m.direction, tMs: m.t_ms })),
    rttMs: dto.rtt_ms,
    retryCase: dto.retry
      ? { msgId: dto.retry.msg_id, label: `타임아웃 → 재전송 · retry ${dto.retry.retry_count}`, lost: dto.retry.lost }
      : undefined,
  };
}
