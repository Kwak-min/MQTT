/* ---------------------------------------------------------------------------
 * 게이트웨이 REST 응답 DTO.
 * 현재 백엔드(Flask)가 실제로 돌려주는 모양 + 이번에 신설을 요청할 필드입니다.
 * `?`가 붙은 필드는 "아직 없음 → 추가 요청" 대상입니다. (README의 API 계약 참고)
 * ------------------------------------------------------------------------- */

/** GET /api/v1/telemetry/latest */
export interface LatestTelemetryDto {
  environment: {
    timestamp: string;
    client_id: string | null;
    temp: number | null;
    hum: number | null;
    /** 게이트웨이는 Ω로 보냅니다. 화면은 kΩ로 표시하므로 매퍼에서 나눕니다. */
    gas: number | null;
    gas_valid: boolean;
    msg_id?: number;
    qos?: 0 | 1 | 2;
    topic_id?: number;
    /** ↓ 신설 요청 항목 */
    rssi?: number;
    battery?: number;
    nn_score?: number;
    network_status?: 0 | 1;
    data_urgency?: 0 | 1;
  };
  power: Record<
    string,
    {
      timestamp: string;
      estimated_energy_mwh: number;
      rtt_ms: number;
      retry_count: number;
      sleep_mode_ratio: number;
      packet_count?: number;
      total_bytes?: number;
    }
  >;
  server_time: string;
}

/** GET /api/sessions/stats */
export interface SessionStatsDto {
  active: number;
  asleep: number;
  timed_out: number;
  total: number;
}

/** GET /api/sessions */
export interface SessionDto {
  client_id: string;
  addr_ip: string;
  addr_port: number;
  status: string;
  packet_count: number;
  connected_at: string;
}

/** GET /api/config */
export interface ConfigDto {
  NETWORK: { RSSI_THRESHOLD: number; PACKET_LOSS_LIMIT: number };
  ENVIRONMENT: { GAS_THRESHOLD_KOHM: number; TEMP_THRESHOLD_CELSIUS: number };
  POWER_MANAGEMENT: { POWER_MODE: string; CURRENT_BATTERY_LEVEL: number };
}

/** GET /api/v1/logs/telemetry?limit= */
export interface TelemetryLogDto {
  count: number;
  data: {
    timestamp: string;
    client_id: string | null;
    msg_id: number;
    qos: 0 | 1 | 2;
    topic_id: number;
    temp: number | null;
    hum: number | null;
    gas: number | null;
    gas_valid: boolean;
    /** ↓ 신설 요청 항목 */
    msg_type?: string;
    network_status?: 0 | 1;
    data_urgency?: 0 | 1;
    delivered?: boolean;
    retry_count?: number;
    rtt_ms?: number | null;
  }[];
}

/** GET /api/v1/stats/summary — 신설 요청 (집계 엔드포인트) */
export interface StatsSummaryDto {
  window: { from: string; to: string; sample_count: number };
  injected_loss_pct?: number;
  systems: {
    gingerbread: SystemStatsDto;
    legacy: SystemStatsDto;
  };
}

export interface SystemStatsDto {
  success_rate_pct: number;
  packet_loss_pct: number;
  avg_latency_ms: number;
  cumulative_energy_mwh: number;
  retry_per_100: number;
  qos_distribution: { qos: 0 | 1 | 2; share_pct: number }[];
  /** [{minute, value}] */
  energy_series?: { minute: number; value: number }[];
  latency_series?: { minute: number; value: number }[];
}

/** GET /api/v1/protocol/handshake?qos= — 신설 요청 */
export interface HandshakeDto {
  qos: 0 | 1 | 2;
  msg_id: string;
  messages: { name: string; direction: 'up' | 'down'; t_ms: number }[];
  rtt_ms: number;
  retry?: { msg_id: string; retry_count: number; lost: boolean };
}

/** WS /ws/telemetry 프레임 */
export type TelemetryFrameDto =
  | { type: 'connected'; message: string; timestamp: string }
  | { type: 'heartbeat'; timestamp: string }
  | { type: 'telemetry'; data: LatestTelemetryDto['environment'] & { estimated_energy_mwh: number | null } };
