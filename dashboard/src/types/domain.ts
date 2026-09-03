/* ---------------------------------------------------------------------------
 * 화면이 소비하는 도메인 모델.
 * 백엔드 DTO(api.ts)와 의도적으로 분리되어 있습니다. 백엔드 스키마가 바뀌면
 * data/http/mappers.ts만 고치고 이 파일과 컴포넌트는 그대로 둡니다.
 * ------------------------------------------------------------------------- */

export type QosLevel = 0 | 1 | 2;

/** 상태는 색이 아니라 의미로 표현합니다. 렌더링 시 아이콘 + 라벨이 항상 붙습니다. */
export type StatusLevel = 'normal' | 'warning' | 'serious' | 'critical';

export type Trend = 'higher-is-better' | 'lower-is-better';

export interface Threshold {
  /** 임계값 (지표와 같은 단위) */
  value: number;
  /** 게이지에 표시할 문구. 예: "임계 100 ms" */
  label: string;
  /** value를 넘으면 위반인지(max), 밑돌면 위반인지(min) */
  direction: 'max' | 'min';
}

export interface Baseline {
  /** 베이스라인(Legacy MQTT) 값 */
  value: number;
  label: string;
  trend: Trend;
}

/** 상단 KPI 카드 1장 */
export interface KpiMetric {
  key: 'rssi' | 'successRate' | 'energy' | 'battery';
  label: string;
  value: number;
  unit: string;
  /** 소수점 자리수 */
  precision: number;
  /** 스파크라인용 최근 값들. 빈 배열이면 스파크라인을 그리지 않습니다. */
  history: number[];
  /** battery처럼 추세보다 잔량이 중요한 지표는 'bar'로 렌더링합니다. */
  sparkKind: 'line' | 'bar';
  status: StatusLevel;
  threshold?: Threshold;
  baseline?: Baseline;
  /** 베이스라인이 없을 때 하단에 노출할 보조 문구 */
  note?: string;
}

/** 얇은 상태 스트립 */
export interface NodeStatus {
  nodeName: string;
  hardware: string;
  ip: string;
  sessionId: string;
  uptimeSeconds: number;
  network: { level: StatusLevel; label: string };
  urgency: { level: StatusLevel; label: string; detail?: string };
  currentQos: QosLevel;
  gatewayConnected: boolean;
  /** ISO 8601. 오프셋 포함 권장 (백엔드 협의 항목) */
  updatedAt: string;
}

/** 임계선이 달린 단일 축 게이지 — Network Quality 패널 */
export interface GaugeMetric {
  key: string;
  label: string;
  labelEn: string;
  value: number;
  unit: string;
  precision: number;
  /** 게이지 축. 지표마다 자기 축을 갖습니다. */
  scale: { min: number; max: number };
  threshold: Threshold;
  status: StatusLevel;
}

export interface SeriesPoint {
  /** ISO 8601 */
  t: string;
  value: number;
}

/** 환경 센서 — 단위가 다르므로 이중 축이 아니라 별도 차트로 나눠 그립니다. */
export interface EnvironmentSeries {
  key: 'gas' | 'temp';
  label: string;
  unit: string;
  precision: number;
  axis: { min: number; max: number };
  threshold?: Threshold;
  points: SeriesPoint[];
}

export interface PowerTrendPoint {
  minute: number;
  gingerbread: number;
  legacy: number;
}

export interface PowerTrend {
  unit: string;
  points: PowerTrendPoint[];
}

export interface AiDecisionInput {
  label: string;
  value: number;
  unit: string;
  precision: number;
}

export interface AiDecision {
  inputs: AiDecisionInput[];
  /** 신경망 위험 확률 0..1 */
  score: number;
  /** QoS 상향 경계 */
  bands: { qos1: number; qos2: number };
  selectedQos: QosLevel;
  reason: string;
  lastSwitch?: { at: string; from: QosLevel; to: QosLevel; cause: string };
}

export interface ConfigEntry {
  key: string;
  label: string;
  value: string;
}

export interface PacketLogRow {
  id: string;
  time: string;
  msgId: string;
  type: string;
  qos: QosLevel;
  urgency: { level: StatusLevel; label: string };
  network: { level: StatusLevel; label: string };
  result: { level: StatusLevel; label: string };
  retry: number;
  rttMs: number | null;
}

/** 운영 화면 한 번에 필요한 전부 */
export interface OperationsSnapshot {
  node: NodeStatus;
  kpis: KpiMetric[];
  environment: EnvironmentSeries[];
  networkQuality: GaugeMetric[];
  powerTrend: PowerTrend;
  aiDecision: AiDecision;
  config: ConfigEntry[];
  packetLog: PacketLogRow[];
}

/* ---- 실험 화면 ---- */

export interface ComparisonMetric {
  key: string;
  label: string;
  unit: string;
  precision: number;
  gingerbread: number;
  legacy: number;
  trend: Trend;
  /** 표본 수·주입 손실률 등 실험 조건. 수치를 인용 가능하게 만드는 문구. */
  context?: string;
}

export interface QosDistributionRow {
  system: 'gingerbread' | 'legacy';
  label: string;
  segments: { qos: QosLevel; share: number }[];
  note?: string;
}

export interface ExperimentSummary {
  /** 상단 요약 KPI */
  headline: ComparisonMetric[];
  successRate: ComparisonMetric;
  energy: ComparisonMetric;
  qosDistribution: QosDistributionRow[];
  latencyTrend: { minute: number; gingerbread: number; legacy: number }[];
  summaryText: string;
}

export interface ExperimentQuery {
  qos?: QosLevel | 'dynamic' | 'all';
  /** 분 단위 */
  rangeMinutes?: number;
}

/* ---- 프로토콜 화면 ---- */

export interface HandshakeMessage {
  name: string;
  direction: 'up' | 'down';
  /** 트랜잭션 시작 기준 경과 시간(ms). 시간축은 이 값에 비례합니다. */
  tMs: number;
}

export interface HandshakeRetryCase {
  msgId: string;
  label: string;
  lost: boolean;
}

export interface HandshakeFlow {
  qos: QosLevel;
  msgId: string;
  messages: HandshakeMessage[];
  rttMs: number;
  retryCase?: HandshakeRetryCase;
}

export interface SessionOverview {
  activeSessions: number;
  totalSessions: number;
  gatewayOnline: boolean;
  gatewayPort: number;
  gatewayTransport: 'UDP' | 'TCP';
  keepAliveSeconds: number | null;
}

export interface ProtocolSnapshot {
  session: SessionOverview;
  flows: HandshakeFlow[];
  packetMetadata: { field: string; value: string }[];
  timeline: { at: string; event: string; detail: string; level: StatusLevel }[];
}
