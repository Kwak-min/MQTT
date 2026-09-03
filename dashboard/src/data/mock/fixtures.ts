/* ---------------------------------------------------------------------------
 * 시안(캔버스) 기준 고정 데이터.
 * 실측 데이터가 들어오면 이 파일은 통째로 버리고 http 소스만 쓰면 됩니다.
 * 수치 정합성 메모: 패킷 손실 1.5% ↔ 전송 성공률 98.5% (100 - 1.5)
 * ------------------------------------------------------------------------- */
import type {
  AiDecision,
  ComparisonMetric,
  ConfigEntry,
  EnvironmentSeries,
  ExperimentSummary,
  GaugeMetric,
  HandshakeFlow,
  KpiMetric,
  NodeStatus,
  OperationsSnapshot,
  PacketLogRow,
  PowerTrend,
  ProtocolSnapshot,
  QosDistributionRow,
} from '@/types/domain';

const BASE_DATE = '2026-09-02';
const at = (hhmmss: string) => `${BASE_DATE}T${hhmmss}+09:00`;

export const node: NodeStatus = {
  nodeName: 'NODE 1',
  hardware: 'ESP32-S3',
  ip: '192.168.0.42',
  sessionId: 'nodeB_001',
  uptimeSeconds: 3862,
  network: { level: 'serious', label: '네트워크 불안정' },
  urgency: { level: 'critical', label: '데이터 긴급', detail: '가스 농도 임계 초과' },
  currentQos: 2,
  gatewayConnected: true,
  updatedAt: at('17:50:05'),
};

export const kpis: KpiMetric[] = [
  {
    key: 'rssi',
    label: 'RSSI',
    value: -84,
    unit: 'dBm',
    precision: 0,
    history: [-72, -74, -71, -78, -77, -82, -83, -84],
    sparkKind: 'line',
    status: 'serious',
    threshold: { value: -80, label: '임계 −80 dBm 미달', direction: 'min' },
  },
  {
    key: 'successRate',
    label: '전송 성공률',
    value: 98.5,
    unit: '%',
    precision: 1,
    history: [96.2, 96.9, 96.0, 97.6, 97.4, 98.1, 98.3, 98.5],
    sparkKind: 'line',
    status: 'normal',
    baseline: { value: 72.0, label: '베이스라인 72.0% 대비', trend: 'higher-is-better' },
  },
  {
    key: 'energy',
    label: '누적 소비 전력',
    value: 42.1,
    unit: 'mWh',
    precision: 1,
    history: [0, 7, 14, 21, 28, 35, 42.1],
    sparkKind: 'line',
    status: 'normal',
    baseline: { value: 124.5, label: '베이스라인 124.5 mWh 대비', trend: 'lower-is-better' },
  },
  {
    key: 'battery',
    label: '배터리',
    value: 85,
    unit: '%',
    precision: 0,
    history: [85],
    sparkKind: 'bar',
    status: 'normal',
    note: 'EXTERNAL_5V · 가상 배터리 값',
  },
];

export const environment: EnvironmentSeries[] = [
  {
    key: 'gas',
    label: '가스 저항',
    unit: 'kΩ',
    precision: 1,
    axis: { min: 17, max: 24 },
    threshold: { value: 20, label: '임계 20.0 이하', direction: 'min' },
    points: [
      { t: at('17:44:00'), value: 23.2 },
      { t: at('17:44:36'), value: 23.5 },
      { t: at('17:45:12'), value: 22.8 },
      { t: at('17:45:48'), value: 23.0 },
      { t: at('17:46:24'), value: 22.1 },
      { t: at('17:47:00'), value: 22.3 },
      { t: at('17:47:36'), value: 21.2 },
      { t: at('17:48:12'), value: 20.6 },
      { t: at('17:48:48'), value: 19.6 },
      { t: at('17:49:24'), value: 18.9 },
      { t: at('17:50:00'), value: 18.5 },
    ],
  },
  {
    key: 'temp',
    label: '온도',
    unit: '°C',
    precision: 1,
    axis: { min: 24, max: 36 },
    threshold: { value: 45, label: '임계 45.0 이상', direction: 'max' },
    points: [
      { t: at('17:44:00'), value: 28.1 },
      { t: at('17:44:36'), value: 28.8 },
      { t: at('17:45:12'), value: 28.5 },
      { t: at('17:45:48'), value: 29.8 },
      { t: at('17:46:24'), value: 30.2 },
      { t: at('17:47:00'), value: 31.0 },
      { t: at('17:47:36'), value: 30.7 },
      { t: at('17:48:12'), value: 31.8 },
      { t: at('17:48:48'), value: 32.1 },
      { t: at('17:49:24'), value: 32.4 },
      { t: at('17:50:00'), value: 32.5 },
    ],
  },
];

export const networkQuality: GaugeMetric[] = [
  {
    key: 'latency',
    label: '지연 시간',
    labelEn: 'Latency',
    value: 45,
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
    value: 1.5,
    unit: '%',
    precision: 1,
    scale: { min: 0, max: 10 },
    threshold: { value: 5, label: '허용 한계 5 %', direction: 'max' },
    status: 'normal',
  },
  {
    key: 'rssi',
    label: '신호 강도',
    labelEn: 'RSSI',
    value: -84,
    unit: 'dBm',
    precision: 0,
    scale: { min: -100, max: -40 },
    threshold: { value: -80, label: '임계 −80 dBm', direction: 'min' },
    status: 'serious',
  },
  {
    key: 'retry',
    label: '재전송',
    labelEn: 'Retry',
    value: 3,
    unit: '회 / 100 패킷',
    precision: 0,
    scale: { min: 0, max: 20 },
    threshold: { value: 10, label: '임계 10회', direction: 'max' },
    status: 'normal',
  },
];

export const powerTrend: PowerTrend = {
  unit: 'mWh',
  points: [
    { minute: 0, gingerbread: 0, legacy: 0 },
    { minute: 10, gingerbread: 7.0, legacy: 21.0 },
    { minute: 20, gingerbread: 14.0, legacy: 42.0 },
    { minute: 30, gingerbread: 21.0, legacy: 63.0 },
    { minute: 40, gingerbread: 28.0, legacy: 83.0 },
    { minute: 50, gingerbread: 35.0, legacy: 104.0 },
    { minute: 60, gingerbread: 42.1, legacy: 124.5 },
  ],
};

export const aiDecision: AiDecision = {
  inputs: [
    { label: '가스 저항', value: 18.5, unit: 'kΩ', precision: 1 },
    { label: '온도', value: 32.5, unit: '°C', precision: 1 },
    { label: 'RSSI', value: -84, unit: 'dBm', precision: 0 },
    { label: '배터리', value: 85, unit: '%', precision: 0 },
  ],
  score: 0.512,
  bands: { qos1: 0.4, qos2: 0.75 },
  selectedQos: 2,
  reason: '점수는 0.40–0.75 구간이지만 네트워크 불안정으로 한 단계 상향',
  lastSwitch: { at: at('17:50:05'), from: 1, to: 2, cause: '네트워크 불안정' },
};

export const config: ConfigEntry[] = [
  { key: 'powerMode', label: '전원 모드', value: 'EXTERNAL_5V' },
  { key: 'rssiThreshold', label: 'RSSI 임계', value: '−80 dBm' },
  { key: 'gasThreshold', label: '가스 임계', value: '20.0 kΩ' },
  { key: 'tempThreshold', label: '온도 임계', value: '45.0 °C' },
  { key: 'sleepInterval', label: 'Sleep 주기', value: '1,000 ms' },
  { key: 'topic', label: '토픽', value: '/sensor/gas' },
];

export const packetLog: PacketLogRow[] = [
  {
    id: '0x1A4B',
    time: at('17:50:05'),
    msgId: '0x1A4B',
    type: 'PUBLISH',
    qos: 2,
    urgency: { level: 'critical', label: '긴급' },
    network: { level: 'serious', label: '불안정' },
    result: { level: 'normal', label: '전달' },
    retry: 0,
    rttMs: 18.4,
  },
  {
    id: '0x1A4A',
    time: at('17:50:03'),
    msgId: '0x1A4A',
    type: 'PUBLISH',
    qos: 1,
    urgency: { level: 'normal', label: '정상' },
    network: { level: 'serious', label: '불안정' },
    result: { level: 'normal', label: '전달' },
    retry: 1,
    rttMs: 31.7,
  },
  {
    id: '0x1A49',
    time: at('17:50:01'),
    msgId: '0x1A49',
    type: 'PUBLISH',
    qos: 0,
    urgency: { level: 'normal', label: '정상' },
    network: { level: 'normal', label: '양호' },
    result: { level: 'normal', label: '전달' },
    retry: 0,
    rttMs: 4.2,
  },
  {
    id: '0x1A48',
    time: at('17:49:58'),
    msgId: '0x1A48',
    type: 'PUBLISH',
    qos: 1,
    urgency: { level: 'normal', label: '정상' },
    network: { level: 'serious', label: '불안정' },
    result: { level: 'critical', label: '유실' },
    retry: 3,
    rttMs: null,
  },
];

export const operationsSnapshot: OperationsSnapshot = {
  node,
  kpis,
  environment,
  networkQuality,
  powerTrend,
  aiDecision,
  config,
  packetLog,
};

/* ---- 실험 ---- */

const headline: ComparisonMetric[] = [
  {
    key: 'energy',
    label: '누적 소비 전력',
    unit: 'mWh',
    precision: 1,
    gingerbread: 42.1,
    legacy: 124.5,
    trend: 'lower-is-better',
    context: '60분 구간',
  },
  {
    key: 'packetLoss',
    label: '패킷 손실',
    unit: '%',
    precision: 1,
    gingerbread: 1.5,
    legacy: 28.0,
    trend: 'lower-is-better',
    context: '손실 20% 주입',
  },
  {
    key: 'successRate',
    label: '전송 성공률',
    unit: '%',
    precision: 1,
    gingerbread: 98.5,
    legacy: 72.0,
    trend: 'higher-is-better',
    context: 'n = 1,200',
  },
  {
    key: 'latency',
    label: '평균 지연',
    unit: 'ms',
    precision: 1,
    gingerbread: 45.0,
    legacy: 68.2,
    trend: 'lower-is-better',
    context: 'PUBLISH→최종 ACK',
  },
];

const qosDistribution: QosDistributionRow[] = [
  {
    system: 'gingerbread',
    label: 'Gingerbread',
    segments: [
      { qos: 0, share: 70 },
      { qos: 1, share: 20 },
      { qos: 2, share: 10 },
    ],
  },
  {
    system: 'legacy',
    label: 'Legacy MQTT',
    segments: [{ qos: 1, share: 100 }],
    note: 'QoS 1 고정',
  },
];

export const experimentSummary: ExperimentSummary = {
  headline,
  successRate: headline[2],
  energy: headline[0],
  qosDistribution,
  latencyTrend: [
    { minute: 0, gingerbread: 38, legacy: 30 },
    { minute: 10, gingerbread: 42, legacy: 36 },
    { minute: 20, gingerbread: 52, legacy: 44 },
    { minute: 30, gingerbread: 55, legacy: 52 },
    { minute: 40, gingerbread: 50, legacy: 68 },
    { minute: 50, gingerbread: 46, legacy: 84 },
    { minute: 60, gingerbread: 45, legacy: 96 },
  ],
  summaryText:
    '전송의 70%를 QoS 0으로 내려 보내면서도 성공률을 유지한 것이 절전의 실제 원인입니다. 동일한 20% 손실 주입 환경에서 Gingerbread는 98.5%, Legacy MQTT는 72.0%를 기록했습니다.',
};

/* ---- 프로토콜 ---- */

export const handshakeFlows: HandshakeFlow[] = [
  {
    qos: 0,
    msgId: '0x1A49',
    messages: [{ name: 'PUBLISH', direction: 'up', tMs: 0 }],
    rttMs: 4.2,
  },
  {
    qos: 1,
    msgId: '0x1A4A',
    messages: [
      { name: 'PUBLISH', direction: 'up', tMs: 0 },
      { name: 'PUBACK', direction: 'down', tMs: 9.6 },
    ],
    rttMs: 9.6,
    retryCase: { msgId: '0x1A48', label: '타임아웃 → 재전송 · retry 1', lost: true },
  },
  {
    qos: 2,
    msgId: '0x1A4B',
    messages: [
      { name: 'PUBLISH', direction: 'up', tMs: 0 },
      { name: 'PUBREC', direction: 'down', tMs: 5.8 },
      { name: 'PUBREL', direction: 'up', tMs: 11.2 },
      { name: 'PUBCOMP', direction: 'down', tMs: 18.4 },
    ],
    rttMs: 18.4,
    retryCase: { msgId: '0x1A48', label: '타임아웃 → 재전송 · retry 1', lost: true },
  },
];

export const protocolSnapshot: ProtocolSnapshot = {
  session: {
    activeSessions: 1,
    totalSessions: 1,
    gatewayOnline: true,
    gatewayPort: 5000,
    gatewayTransport: 'UDP',
    keepAliveSeconds: null,
  },
  flows: handshakeFlows,
  packetMetadata: [
    { field: 'Msg Type', value: 'PUBLISH (0x02)' },
    { field: 'Length', value: '136 bytes' },
    { field: 'Topic ID', value: '0x0002' },
    { field: 'Msg ID', value: '0x1A4B' },
    { field: 'QoS', value: '2' },
    { field: 'Meta', value: 'net=1 urgency=1' },
  ],
  timeline: [
    { at: at('17:50:01'), event: 'CONNECT', detail: 'Node 1 세션 수립', level: 'normal' },
    { at: at('17:49:58'), event: 'TIMEOUT', detail: '0x1A48 유실 · retry 3회 후 포기', level: 'critical' },
    { at: at('17:50:05'), event: 'QoS 상향', detail: 'QoS 1 → 2 (네트워크 불안정)', level: 'serious' },
  ],
};
