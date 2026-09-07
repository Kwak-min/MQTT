/**
 * 도메인 타입.
 *
 * 백엔드와 아직 합의되지 않은 항목은 docs/API-CONTRACT.md 의
 * "합의 필요" 표에 정리되어 있습니다. 이 타입들은 그 문서의 제안안을
 * 그대로 반영한 것으로, 실제 계약이 확정되면 이 파일만 고치면 됩니다.
 */

export type FirmwareVariant = "standard_mqtt" | "monitor" | "gingerbread";

export type NodeRole = "primary" | "baseline";

export type ConnectionState = "connected" | "connecting" | "disconnected" | "error";

/** Gingerbread 전용 duty-cycle 동작 상태. 다른 펌웨어는 항상 undefined. */
export type DeviceState = "active" | "asleep";

export interface NodeInfo {
  nodeId: string;
  name: string;
  firmware: FirmwareVariant;
  role: NodeRole;
  /** 논의 중: baseline(Node 2) 적재 방식이 MQTT subscribe 인지 별도 수집기인지 */
  ingestPath: "mqtt" | "udp" | "collector";
}

/** 센서 원본값. gas_resistance 는 옴(Ω) 단위로 전송된다고 가정 — 화면 표시는 kΩ. */
export interface TelemetrySample {
  msgId: number;
  nodeId: string;
  /** ISO 8601 + 오프셋, 예: 2026-09-03T10:41:07+09:00 */
  timestamp: string;
  temperatureC: number;
  humidityPct: number;
  pressureHpa: number;
  gasResistanceOhm: number;
  rssiDbm: number;
  batteryV: number;
  uptimeS: number;
  /** BME680 가스 센서 정상 범위 여부. 합의 필요 항목(API-CONTRACT #6) — 없으면 뱃지를 숨김. */
  gasValid?: boolean;
  /** Gingerbread 노드의 송신/수면 상태. Standard MQTT/Monitor 는 항상 undefined. */
  deviceState?: DeviceState;
  /** 최신 수신 패킷 원문(JSON, 펌웨어 필드명 그대로). 페이로드 뷰어용, 합의 필요 항목. */
  rawPayload?: string;
}

export type SessionStatus = "running" | "completed" | "error" | "aborted";

export interface SessionSummary {
  sessionId: string;
  nodeId: string;
  protocol: "mqtt" | "udp";
  qos: 0 | 1 | 2;
  status: SessionStatus;
  startedAt: string;
  endedAt: string | null;
  sampleCount: number;
  droppedCount: number;
  note?: string;
}

export interface PowerSample {
  timestamp: string;
  nodeId: string;
  /** power.csv 에 msg_id 조인 키 추가 여부가 미확정이라 optional 로 둠 */
  msgId?: number;
  voltageV: number;
  currentMa: number;
  powerMw: number;
  estimatedBatteryPct: number;
  /** 1회 통신(발행) 당 소모 에너지 (mJ). 합의 필요 항목(API-CONTRACT #6) — 없으면 에너지 KPI를 숨김. */
  energyMj?: number;
  /** 초기 Wi-Fi 연결 등으로 발생한 이상치 여부 — 스파이크 필터 토글이 이 값을 기준으로 제외. */
  isConnectionSpike?: boolean;
}

export interface ProtocolStats {
  nodeId: string;
  windowLabel: string;
  delivered: number;
  duplicate: number;
  outOfOrder: number;
  parseErrors: number;
  avgLatencyMs: number;
  lastQos: 0 | 1 | 2;
}

export type ControlCommandKind =
  | "set_report_interval"
  | "reboot"
  | "recalibrate_gas_baseline"
  | "toggle_relay";

export type ControlAckStatus = "pending" | "acked" | "failed";

export interface ControlCommand {
  commandId: string;
  nodeId: string;
  kind: ControlCommandKind;
  payload: Record<string, string | number | boolean>;
  issuedAt: string;
  ackStatus: ControlAckStatus;
  ackAt: string | null;
}

export interface NodeConfig {
  nodeId: string;
  reportIntervalS: number;
  gasBaselineOhm: number;
  tempWarnC: number;
  humidityWarnPct: number;
  mqttTopic: string;
  qos: 0 | 1 | 2;
}

export interface StatsSummary {
  nodeId: string;
  /** 합의 필요: 세션 기준 window 인지 시각(예: 최근 1시간) 기준 window 인지 */
  windowKind: "session" | "time";
  windowLabel: string;
  avgTemperatureC: number;
  avgHumidityPct: number;
  avgGasResistanceOhm: number;
  sampleCount: number;
}

/**
 * 프론트엔드가 필요로 하는 모든 읽기/쓰기 동작의 최소 계약.
 * mock ↔ http 구현을 이 인터페이스 하나로 교체합니다.
 */
export interface TelemetrySource {
  listNodes(): Promise<NodeInfo[]>;

  getLatestTelemetry(nodeId: string): Promise<TelemetrySample | null>;
  getTelemetryHistory(nodeId: string, limitSamples: number): Promise<TelemetrySample[]>;
  /** 실시간 스트림 구독. 구독 해제 함수를 반환합니다. */
  subscribeTelemetry(nodeId: string, onSample: (sample: TelemetrySample) => void): () => void;

  listSessions(nodeId?: string): Promise<SessionSummary[]>;
  getSession(sessionId: string): Promise<SessionSummary | null>;

  getPowerHistory(nodeId: string, limitSamples: number): Promise<PowerSample[]>;

  /** 로그 헤더만 남기고 데이터 본문을 비웁니다(파일 삭제 아님). */
  truncateTelemetry(nodeId: string): Promise<{ truncatedAt: string }>;
  /** 텔레메트리 로그 전체를 CSV 로 내보냅니다. */
  exportTelemetryCsv(nodeId: string): Promise<Blob>;

  getProtocolStats(nodeId: string): Promise<ProtocolStats>;

  getStatsSummary(nodeId: string): Promise<StatsSummary>;

  getNodeConfig(nodeId: string): Promise<NodeConfig>;
  updateNodeConfig(nodeId: string, patch: Partial<NodeConfig>): Promise<NodeConfig>;

  listControlCommands(nodeId: string): Promise<ControlCommand[]>;
  sendControlCommand(
    nodeId: string,
    kind: ControlCommandKind,
    payload: Record<string, string | number | boolean>
  ): Promise<ControlCommand>;

  getConnectionState(): ConnectionState;
  onConnectionStateChange(listener: (state: ConnectionState) => void): () => void;
}
