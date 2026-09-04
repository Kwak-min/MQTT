const INTERVAL_OPTIONS = [1, 5, 10, 30, 60];

interface ExportTarget {
  nodeId: string;
  label: string;
}

export function ExperimentControlPanel({
  activeIntervalS,
  applyingInterval,
  onSetInterval,
  onTruncate,
  truncating,
  onExportCsv,
  exportingNodeId,
  statusLabel,
  statusColor,
  exportTargets,
}: {
  activeIntervalS: number | null;
  applyingInterval: boolean;
  onSetInterval: (seconds: number) => void;
  onTruncate: () => void;
  truncating: boolean;
  onExportCsv: (nodeId: string) => void;
  exportingNodeId: string | null;
  statusLabel: string;
  statusColor: string;
  exportTargets: ExportTarget[];
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <div>
        <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>
          전송 주기 (두 보드 동시 적용)
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {INTERVAL_OPTIONS.map((s) => {
            const active = activeIntervalS === s;
            return (
              <button
                key={s}
                className="btn"
                disabled={applyingInterval}
                onClick={() => onSetInterval(s)}
                style={{
                  borderColor: active ? "var(--signal-teal)" : "var(--border-hair-strong)",
                  background: active ? "var(--bg-panel-raised)" : "var(--bg-inset)",
                }}
              >
                {s}초
              </button>
            );
          })}
          {applyingInterval && (
            <span style={{ fontSize: 12, color: "var(--text-tertiary)", alignSelf: "center" }}>적용 중…</span>
          )}
        </div>
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center" }}>
        <button className="btn" disabled={truncating} onClick={onTruncate}>
          {truncating ? "초기화 중…" : "로그 초기화 (Truncate)"}
        </button>

        {exportTargets.map((t) => (
          <button
            key={t.nodeId}
            className="btn btn-primary"
            disabled={exportingNodeId !== null}
            onClick={() => onExportCsv(t.nodeId)}
          >
            {exportingNodeId === t.nodeId ? "내보내는 중…" : `${t.label} CSV 다운로드`}
          </button>
        ))}

        <span className="tag" style={{ borderColor: statusColor, color: statusColor, marginLeft: "auto" }}>
          <span className="dot" style={{ background: statusColor }} />
          {statusLabel}
        </span>
      </div>
    </div>
  );
}
