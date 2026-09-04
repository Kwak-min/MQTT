interface PayloadOption {
  nodeId: string;
  label: string;
  color: string;
  payload: string | null;
}

export function PayloadViewer({
  options,
  selectedNodeId,
  onSelect,
}: {
  options: PayloadOption[];
  selectedNodeId: string | null;
  onSelect: (nodeId: string) => void;
}) {
  const active = options.find((o) => o.nodeId === selectedNodeId) ?? options[0] ?? null;

  return (
    <div>
      <div style={{ display: "flex", gap: 6, marginBottom: 10 }}>
        {options.map((opt) => {
          const isActive = opt.nodeId === active?.nodeId;
          return (
            <button
              key={opt.nodeId}
              className="btn"
              onClick={() => onSelect(opt.nodeId)}
              style={{
                padding: "6px 10px",
                borderColor: isActive ? opt.color : "var(--border-hair-strong)",
                background: isActive ? "var(--bg-panel-raised)" : "var(--bg-inset)",
              }}
            >
              <span className="dot" style={{ background: opt.color }} />
              <span style={{ fontSize: 12 }}>{opt.label}</span>
            </button>
          );
        })}
      </div>
      {active?.payload ? (
        <pre
          className="mono"
          style={{
            margin: 0,
            padding: 12,
            background: "var(--bg-inset)",
            border: "1px solid var(--border-hair)",
            borderRadius: "var(--radius-sm)",
            fontSize: 12,
            lineHeight: 1.5,
            overflowX: "auto",
            maxHeight: 220,
          }}
        >
          {active.payload}
        </pre>
      ) : (
        <div className="empty-state">수신된 페이로드가 없습니다.</div>
      )}
    </div>
  );
}
