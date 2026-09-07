import type {
  ConnectionState,
  ControlCommand,
  NodeConfig,
  NodeInfo,
  PowerSample,
  ProtocolStats,
  SessionSummary,
  StatsSummary,
  TelemetrySample,
  TelemetrySource,
} from "./types";

const NODES: NodeInfo[] = [
  {
    nodeId: "node-01",
    name: "Standard MQTT",
    firmware: "standard_mqtt",
    role: "primary",
    ingestPath: "mqtt",
  },
  {
    nodeId: "node-02",
    name: "Baseline (Gingerbread)",
    firmware: "gingerbread",
    role: "baseline",
    ingestPath: "collector",
  },
  {
    nodeId: "node-03",
    name: "Monitor",
    firmware: "monitor",
    role: "primary",
    ingestPath: "udp",
  },
];

function isoNow(offsetMs = 0): string {
  return new Date(Date.now() + offsetMs).toISOString().replace("Z", "+00:00");
}

function seededRandom(seed: number) {
  let s = seed % 2147483647;
  if (s <= 0) s += 2147483646;
  return () => {
    s = (s * 16807) % 2147483647;
    return (s - 1) / 2147483646;
  };
}

class NodeSimState {
  msgId = 0;
  temperatureC: number;
  humidityPct: number;
  pressureHpa = 1013 + Math.random() * 6 - 3;
  gasResistanceOhm: number;
  batteryV = 4.05 + Math.random() * 0.1;
  uptimeS = Math.floor(Math.random() * 50_000);
  rand: () => number;

  constructor(seed: number, baseTemp: number, baseGasOhm: number) {
    this.rand = seededRandom(seed);
    this.temperatureC = baseTemp;
    this.humidityPct = 45 + this.rand() * 10;
    this.gasResistanceOhm = baseGasOhm;
  }

  tick(): TelemetrySample {
    this.msgId += 1;
    this.temperatureC += (this.rand() - 0.5) * 0.3;
    this.humidityPct = clamp(this.humidityPct + (this.rand() - 0.5) * 1.2, 30, 80);
    this.pressureHpa += (this.rand() - 0.5) * 0.4;
    // 가스 저항은 VOC 농도가 오를수록 떨어지는 경향을 단순 시뮬레이션
    this.gasResistanceOhm = clamp(
      this.gasResistanceOhm + (this.rand() - 0.52) * 4000,
      20_000,
      180_000
    );
    this.batteryV = clamp(this.batteryV - 0.00004 + (this.rand() - 0.5) * 0.0005, 3.3, 4.2);
    this.uptimeS += 2;

    const gasResistanceOhm = Math.round(this.gasResistanceOhm);
    const sample: TelemetrySample = {
      msgId: this.msgId,
      nodeId: "",
      timestamp: isoNow(),
      temperatureC: round(this.temperatureC, 2),
      humidityPct: round(this.humidityPct, 1),
      pressureHpa: round(this.pressureHpa, 1),
      gasResistanceOhm,
      rssiDbm: Math.round(-58 - this.rand() * 22),
      batteryV: round(this.batteryV, 3),
      uptimeS: this.uptimeS,
      gasValid: computeGasValid(gasResistanceOhm, this.rand),
    };
    return sample;
  }
}

function clamp(v: number, min: number, max: number) {
  return Math.max(min, Math.min(max, v));
}
function round(v: number, digits: number) {
  const f = 10 ** digits;
  return Math.round(v * f) / f;
}

const FIRMWARE_BY_NODE: Record<string, "standard_mqtt" | "monitor" | "gingerbread"> = {
  "node-01": "standard_mqtt",
  "node-02": "gingerbread",
  "node-03": "monitor",
};

/** BME680 가스 저항이 정상 판정 범위를 벗어나는 경우가 드물게 섞이도록 시뮬레이션 */
function computeGasValid(gasResistanceOhm: number, rand: () => number): boolean {
  if (gasResistanceOhm < 25_000 || gasResistanceOhm > 175_000) return false;
  return rand() > 0.03;
}

/** Gingerbread 는 발행 직후 짧게 ACTIVE, 나머지 구간은 ASLEEP 인 duty-cycle 노드 */
function computeDeviceState(nodeId: string, tick: number): "active" | "asleep" | undefined {
  if (FIRMWARE_BY_NODE[nodeId] !== "gingerbread") return undefined;
  return tick % 5 === 0 ? "active" : "asleep";
}

function buildRawPayload(sample: TelemetrySample): string {
  const firmware = FIRMWARE_BY_NODE[sample.nodeId];
  const payload: Record<string, unknown> = {
    msg_id: sample.msgId,
    node_id: sample.nodeId,
    ts: sample.timestamp,
    temp_c: sample.temperatureC,
    humidity_pct: sample.humidityPct,
    pressure_hpa: sample.pressureHpa,
    gas_ohm: sample.gasResistanceOhm,
    gas_valid: sample.gasValid ?? true,
    rssi_dbm: sample.rssiDbm,
    battery_v: sample.batteryV,
  };
  if (firmware === "gingerbread") {
    payload.device_state = (sample.deviceState ?? "asleep").toUpperCase();
  }
  return JSON.stringify(payload, null, 2);
}

/** 펌웨어별 1회 통신 에너지 프로필 (mJ). Gingerbread(UDP, duty-cycle)가 Standard MQTT(TCP+TLS+keepalive) 대비 크게 낮음. */
const ENERGY_PROFILE_MJ: Record<"standard_mqtt" | "monitor" | "gingerbread", { base: number; jitter: number }> = {
  gingerbread: { base: 11, jitter: 3 },
  standard_mqtt: { base: 38, jitter: 8 },
  monitor: { base: 24, jitter: 6 },
};

const simStates = new Map<string, NodeSimState>([
  ["node-01", new NodeSimState(1, 23.4, 95_000)],
  ["node-02", new NodeSimState(2, 21.8, 110_000)],
  ["node-03", new NodeSimState(3, 24.9, 78_000)],
]);

function historyFor(nodeId: string, count: number): TelemetrySample[] {
  const sim = simStates.get(nodeId);
  if (!sim) return [];
  const out: TelemetrySample[] = [];
  const seed = nodeId === "node-01" ? 11 : nodeId === "node-02" ? 22 : 33;
  const rand = seededRandom(seed);
  let temp = sim.temperatureC - count * 0.02;
  let gas = sim.gasResistanceOhm - count * 30;
  let hum = sim.humidityPct;
  for (let i = 0; i < count; i += 1) {
    temp += (rand() - 0.5) * 0.3;
    hum = clamp(hum + (rand() - 0.5) * 1.1, 30, 80);
    gas = clamp(gas + (rand() - 0.5) * 3800 + 25, 20_000, 180_000);
    const gasResistanceOhm = Math.round(gas);
    const msgId = i + 1;
    const sample: TelemetrySample = {
      msgId,
      nodeId,
      timestamp: isoNow(-(count - i) * 2000),
      temperatureC: round(temp, 2),
      humidityPct: round(hum, 1),
      pressureHpa: round(1013 + Math.sin(i / 20) * 2, 1),
      gasResistanceOhm,
      rssiDbm: Math.round(-58 - rand() * 22),
      batteryV: round(4.1 - i * 0.0002, 3),
      uptimeS: i * 2,
      gasValid: computeGasValid(gasResistanceOhm, rand),
      deviceState: computeDeviceState(nodeId, msgId),
    };
    sample.rawPayload = buildRawPayload(sample);
    out.push(sample);
  }
  return out;
}

const SESSIONS: SessionSummary[] = [
  {
    sessionId: "sess-2026-09-03-a",
    nodeId: "node-01",
    protocol: "mqtt",
    qos: 1,
    status: "running",
    startedAt: isoNow(-1000 * 60 * 42),
    endedAt: null,
    sampleCount: 1260,
    droppedCount: 3,
  },
  {
    sessionId: "sess-2026-09-03-b",
    nodeId: "node-02",
    protocol: "mqtt",
    qos: 0,
    status: "running",
    startedAt: isoNow(-1000 * 60 * 42),
    endedAt: null,
    sampleCount: 1258,
    droppedCount: 11,
    note: "baseline 노드 — 실장 위치 다름",
  },
  {
    sessionId: "sess-2026-09-03-c",
    nodeId: "node-03",
    protocol: "udp",
    qos: 0,
    status: "running",
    startedAt: isoNow(-1000 * 60 * 18),
    endedAt: null,
    sampleCount: 540,
    droppedCount: 0,
  },
  {
    sessionId: "sess-2026-09-02-a",
    nodeId: "node-01",
    protocol: "mqtt",
    qos: 1,
    status: "completed",
    startedAt: isoNow(-1000 * 60 * 60 * 26),
    endedAt: isoNow(-1000 * 60 * 60 * 20),
    sampleCount: 10_800,
    droppedCount: 22,
  },
  {
    sessionId: "sess-2026-09-01-a",
    nodeId: "node-03",
    protocol: "udp",
    qos: 0,
    status: "error",
    startedAt: isoNow(-1000 * 60 * 60 * 50),
    endedAt: isoNow(-1000 * 60 * 60 * 49),
    sampleCount: 210,
    droppedCount: 87,
    note: "패킷 파서 오류로 조기 종료",
  },
];

function powerHistoryFor(nodeId: string, count: number): PowerSample[] {
  const rand = seededRandom(nodeId === "node-01" ? 111 : nodeId === "node-02" ? 222 : 333);
  const firmware = FIRMWARE_BY_NODE[nodeId] ?? "monitor";
  const energyProfile = ENERGY_PROFILE_MJ[firmware];
  const out: PowerSample[] = [];
  let batteryPct = 88 - rand() * 10;
  // 초기 몇 개 샘플은 Wi-Fi 연결(스캔/핸드셰이크) 비용이 섞여 에너지가 튀는 구간을 시뮬레이션
  const spikeCount = Math.min(3, count);
  for (let i = 0; i < count; i += 1) {
    const voltage = clamp(4.1 - i * 0.0003 + (rand() - 0.5) * 0.02, 3.3, 4.2);
    const current = 18 + rand() * 14;
    batteryPct = clamp(batteryPct - 0.01 - rand() * 0.01, 0, 100);
    const isConnectionSpike = i < spikeCount;
    const rawEnergy = energyProfile.base + (rand() - 0.5) * energyProfile.jitter * 2;
    const energyMj = round(isConnectionSpike ? rawEnergy * (4 + rand() * 3) : Math.max(rawEnergy, energyProfile.base * 0.4), 1);
    out.push({
      timestamp: isoNow(-(count - i) * 4000),
      nodeId,
      msgId: i + 1,
      voltageV: round(voltage, 3),
      currentMa: round(current, 1),
      powerMw: round(voltage * current, 1),
      estimatedBatteryPct: round(batteryPct, 1),
      energyMj,
      isConnectionSpike,
    });
  }
  return out;
}

function csvEscape(value: string | number | boolean | undefined): string {
  if (value === undefined) return "";
  const s = String(value);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

const configs = new Map<string, NodeConfig>([
  [
    "node-01",
    {
      nodeId: "node-01",
      reportIntervalS: 2,
      gasBaselineOhm: 95_000,
      tempWarnC: 30,
      humidityWarnPct: 70,
      mqttTopic: "nodes/node-01/telemetry",
      qos: 1,
    },
  ],
  [
    "node-02",
    {
      nodeId: "node-02",
      reportIntervalS: 5,
      gasBaselineOhm: 110_000,
      tempWarnC: 28,
      humidityWarnPct: 65,
      mqttTopic: "nodes/node-02/telemetry",
      qos: 0,
    },
  ],
  [
    "node-03",
    {
      nodeId: "node-03",
      reportIntervalS: 1,
      gasBaselineOhm: 78_000,
      tempWarnC: 32,
      humidityWarnPct: 75,
      mqttTopic: "nodes/node-03/telemetry",
      qos: 0,
    },
  ],
]);

const controlLog = new Map<string, ControlCommand[]>([
  ["node-01", []],
  ["node-02", []],
  ["node-03", []],
]);

export function createMockSource(intervalMs: number): TelemetrySource {
  let connectionState: ConnectionState = "connecting";
  const connectionListeners = new Set<(s: ConnectionState) => void>();
  const subscribers = new Map<string, Set<(s: TelemetrySample) => void>>();

  function setConnectionState(next: ConnectionState) {
    connectionState = next;
    connectionListeners.forEach((l) => l(next));
  }

  // 모의 연결 시퀀스: 진짜 소켓처럼 살짝 지연 후 연결됨을 표시
  setTimeout(() => setConnectionState("connected"), 350);

  setInterval(() => {
    subscribers.forEach((set, nodeId) => {
      if (set.size === 0) return;
      const sim = simStates.get(nodeId);
      if (!sim) return;
      const sample = sim.tick();
      sample.nodeId = nodeId;
      sample.deviceState = computeDeviceState(nodeId, sample.msgId);
      sample.rawPayload = buildRawPayload(sample);
      set.forEach((cb) => cb(sample));
    });
  }, intervalMs);

  return {
    async listNodes() {
      return NODES;
    },

    async getLatestTelemetry(nodeId) {
      const sim = simStates.get(nodeId);
      if (!sim) return null;
      const sample = sim.tick();
      sample.nodeId = nodeId;
      sample.deviceState = computeDeviceState(nodeId, sample.msgId);
      sample.rawPayload = buildRawPayload(sample);
      return sample;
    },

    async getTelemetryHistory(nodeId, limitSamples) {
      return historyFor(nodeId, limitSamples);
    },

    subscribeTelemetry(nodeId, onSample) {
      if (!subscribers.has(nodeId)) subscribers.set(nodeId, new Set());
      const set = subscribers.get(nodeId)!;
      set.add(onSample);
      return () => set.delete(onSample);
    },

    async listSessions(nodeId) {
      return nodeId ? SESSIONS.filter((s) => s.nodeId === nodeId) : SESSIONS;
    },

    async getSession(sessionId) {
      return SESSIONS.find((s) => s.sessionId === sessionId) ?? null;
    },

    async getPowerHistory(nodeId, limitSamples) {
      return powerHistoryFor(nodeId, limitSamples);
    },

    async truncateTelemetry(nodeId) {
      // mock 환경에는 실제 로그 파일이 없어 부작용은 없지만, 백엔드 동작(헤더만 남기고
      // 데이터 본문을 비움)과 동일한 지연·응답 형태를 재현합니다.
      void nodeId;
      await new Promise((r) => setTimeout(r, 300));
      return { truncatedAt: isoNow() };
    },

    async exportTelemetryCsv(nodeId) {
      const history = historyFor(nodeId, 500);
      const header =
        "msg_id,timestamp,temperature_c,humidity_pct,pressure_hpa,gas_resistance_ohm,gas_valid,rssi_dbm,battery_v,uptime_s,device_state\n";
      const rows = history.map((s) =>
        [
          s.msgId,
          s.timestamp,
          s.temperatureC,
          s.humidityPct,
          s.pressureHpa,
          s.gasResistanceOhm,
          s.gasValid,
          s.rssiDbm,
          s.batteryV,
          s.uptimeS,
          s.deviceState ?? "",
        ]
          .map(csvEscape)
          .join(",")
      );
      const csv = header + rows.join("\n") + "\n";
      return new Blob([csv], { type: "text/csv;charset=utf-8;" });
    },

    async getProtocolStats(nodeId) {
      const rand = seededRandom(nodeId.length * 77);
      const delivered = 1200 + Math.floor(rand() * 400);
      return {
        nodeId,
        windowLabel: "최근 1시간",
        delivered,
        duplicate: Math.floor(rand() * 6),
        outOfOrder: Math.floor(rand() * 4),
        parseErrors: nodeId === "node-03" ? Math.floor(rand() * 3) : 0,
        avgLatencyMs: Math.round(40 + rand() * 90),
        lastQos: (configs.get(nodeId)?.qos ?? 0) as 0 | 1 | 2,
      } satisfies ProtocolStats;
    },

    async getStatsSummary(nodeId) {
      const history = historyFor(nodeId, 200);
      const avg = (arr: number[]) => arr.reduce((a, b) => a + b, 0) / arr.length;
      return {
        nodeId,
        windowKind: "time",
        windowLabel: "최근 200개 샘플",
        avgTemperatureC: round(avg(history.map((h) => h.temperatureC)), 2),
        avgHumidityPct: round(avg(history.map((h) => h.humidityPct)), 1),
        avgGasResistanceOhm: Math.round(avg(history.map((h) => h.gasResistanceOhm))),
        sampleCount: history.length,
      } satisfies StatsSummary;
    },

    async getNodeConfig(nodeId) {
      const cfg = configs.get(nodeId);
      if (!cfg) throw new Error(`설정을 찾을 수 없습니다: ${nodeId}`);
      return cfg;
    },

    async updateNodeConfig(nodeId, patch) {
      const cfg = configs.get(nodeId);
      if (!cfg) throw new Error(`설정을 찾을 수 없습니다: ${nodeId}`);
      const next = { ...cfg, ...patch };
      configs.set(nodeId, next);
      await new Promise((r) => setTimeout(r, 250));
      return next;
    },

    async listControlCommands(nodeId) {
      return controlLog.get(nodeId) ?? [];
    },

    async sendControlCommand(nodeId, kind, payload) {
      const cmd: ControlCommand = {
        commandId: `cmd-${Date.now()}`,
        nodeId,
        kind,
        payload,
        issuedAt: isoNow(),
        ackStatus: "pending",
        ackAt: null,
      };
      const log = controlLog.get(nodeId) ?? [];
      log.unshift(cmd);
      controlLog.set(nodeId, log);

      setTimeout(() => {
        cmd.ackStatus = kind === "reboot" && Math.random() < 0.08 ? "failed" : "acked";
        cmd.ackAt = isoNow();
      }, 900);

      return cmd;
    },

    getConnectionState() {
      return connectionState;
    },

    onConnectionStateChange(listener) {
      connectionListeners.add(listener);
      return () => connectionListeners.delete(listener);
    },
  };
}
