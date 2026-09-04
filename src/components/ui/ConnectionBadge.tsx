import { useConnectionState } from "@/hooks/useTelemetrySource";
import { getDataSourceMode } from "@/lib/dataSource";

const STATE_LABEL: Record<string, string> = {
  connected: "연결됨",
  connecting: "연결 중",
  disconnected: "연결 끊김",
  error: "오류",
};

const STATE_COLOR: Record<string, string> = {
  connected: "var(--signal-green)",
  connecting: "var(--signal-amber)",
  disconnected: "var(--text-tertiary)",
  error: "var(--signal-red)",
};

export function ConnectionBadge() {
  const state = useConnectionState();
  const mode = getDataSourceMode();
  const color = STATE_COLOR[state] ?? "var(--text-tertiary)";

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <div className="tag" style={{ borderColor: "var(--border-hair-strong)" }}>
        <span
          className={state === "connected" ? "dot dot-pulse" : "dot"}
          style={{ background: color }}
        />
        <span className="mono">{STATE_LABEL[state] ?? state}</span>
      </div>
      <div
        className="tag mono"
        style={{ borderColor: "var(--border-hair-strong)", color: "var(--text-tertiary)" }}
      >
        {mode === "mock" ? "mock 데이터" : "http 연동"}
      </div>
    </div>
  );
}
