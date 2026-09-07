import type {
  ConnectionState,
  ControlCommand,
  ControlCommandKind,
  NodeConfig,
  NodeInfo,
  PowerSample,
  ProtocolStats,
  SessionSummary,
  StatsSummary,
  TelemetrySample,
  TelemetrySource,
} from "./types";

interface HttpSourceOptions {
  baseUrl: string;
  wsUrl: string;
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    throw new Error(`API 오류 ${res.status}: ${res.statusText}`);
  }
  return (await res.json()) as T;
}

/**
 * docs/API-CONTRACT.md 에 정의된 엔드포인트를 그대로 호출합니다.
 * 경로/필드명이 백엔드와 확정되면 이 파일만 수정하면 됩니다 —
 * 화면 쪽 코드는 TelemetrySource 인터페이스만 알고 있습니다.
 */
export function createHttpSource(opts: HttpSourceOptions): TelemetrySource {
  const { baseUrl, wsUrl } = opts;
  let connectionState: ConnectionState = "connecting";
  const connectionListeners = new Set<(s: ConnectionState) => void>();
  const sockets = new Map<string, WebSocket>();
  const subscribers = new Map<string, Set<(s: TelemetrySample) => void>>();

  function setConnectionState(next: ConnectionState) {
    connectionState = next;
    connectionListeners.forEach((l) => l(next));
  }

  function ensureSocket(nodeId: string) {
    if (sockets.has(nodeId)) return sockets.get(nodeId)!;
    const socket = new WebSocket(`${wsUrl}?node_id=${encodeURIComponent(nodeId)}`);
    setConnectionState("connecting");

    socket.onopen = () => setConnectionState("connected");
    socket.onclose = () => setConnectionState("disconnected");
    socket.onerror = () => setConnectionState("error");
    socket.onmessage = (event) => {
      try {
        const sample = JSON.parse(event.data) as TelemetrySample;
        subscribers.get(nodeId)?.forEach((cb) => cb(sample));
      } catch {
        // 파싱 실패 패킷은 무시 — 통계는 /protocol-stats 엔드포인트에서 집계
      }
    };

    sockets.set(nodeId, socket);
    return socket;
  }

  return {
    async listNodes() {
      return json<NodeInfo[]>(await fetch(`${baseUrl}/api/v1/nodes`));
    },

    async getLatestTelemetry(nodeId) {
      const res = await fetch(`${baseUrl}/api/v1/nodes/${nodeId}/telemetry/latest`);
      if (res.status === 404) return null;
      return json<TelemetrySample>(res);
    },

    async getTelemetryHistory(nodeId, limitSamples) {
      return json<TelemetrySample[]>(
        await fetch(`${baseUrl}/api/v1/nodes/${nodeId}/telemetry?limit=${limitSamples}`)
      );
    },

    subscribeTelemetry(nodeId, onSample) {
      if (!subscribers.has(nodeId)) subscribers.set(nodeId, new Set());
      subscribers.get(nodeId)!.add(onSample);
      ensureSocket(nodeId);
      return () => {
        subscribers.get(nodeId)?.delete(onSample);
        if ((subscribers.get(nodeId)?.size ?? 0) === 0) {
          sockets.get(nodeId)?.close();
          sockets.delete(nodeId);
        }
      };
    },

    async listSessions(nodeId) {
      const qs = nodeId ? `?node_id=${nodeId}` : "";
      return json<SessionSummary[]>(await fetch(`${baseUrl}/api/v1/sessions${qs}`));
    },

    async getSession(sessionId) {
      const res = await fetch(`${baseUrl}/api/v1/sessions/${sessionId}`);
      if (res.status === 404) return null;
      return json<SessionSummary>(res);
    },

    async getPowerHistory(nodeId, limitSamples) {
      return json<PowerSample[]>(
        await fetch(`${baseUrl}/api/v1/nodes/${nodeId}/power?limit=${limitSamples}`)
      );
    },

    async truncateTelemetry(nodeId) {
      return json<{ truncatedAt: string }>(
        await fetch(`${baseUrl}/api/v1/nodes/${nodeId}/telemetry/truncate`, { method: "POST" })
      );
    },

    async exportTelemetryCsv(nodeId) {
      const res = await fetch(`${baseUrl}/api/v1/nodes/${nodeId}/telemetry/export.csv`);
      if (!res.ok) {
        throw new Error(`API 오류 ${res.status}: ${res.statusText}`);
      }
      return res.blob();
    },

    async getProtocolStats(nodeId) {
      return json<ProtocolStats>(await fetch(`${baseUrl}/api/v1/nodes/${nodeId}/protocol-stats`));
    },

    async getStatsSummary(nodeId) {
      return json<StatsSummary>(await fetch(`${baseUrl}/api/v1/stats/summary?node_id=${nodeId}`));
    },

    async getNodeConfig(nodeId) {
      return json<NodeConfig>(await fetch(`${baseUrl}/api/v1/nodes/${nodeId}/config`));
    },

    async updateNodeConfig(nodeId, patch) {
      return json<NodeConfig>(
        await fetch(`${baseUrl}/api/v1/nodes/${nodeId}/config`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(patch),
        })
      );
    },

    async listControlCommands(nodeId) {
      return json<ControlCommand[]>(await fetch(`${baseUrl}/api/v1/nodes/${nodeId}/control`));
    },

    async sendControlCommand(nodeId, kind, payload) {
      return json<ControlCommand>(
        await fetch(`${baseUrl}/api/v1/nodes/${nodeId}/control`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ kind, payload } satisfies {
            kind: ControlCommandKind;
            payload: Record<string, string | number | boolean>;
          }),
        })
      );
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
