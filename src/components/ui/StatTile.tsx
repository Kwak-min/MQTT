export function StatTile({
  label,
  value,
  unit,
  accent,
  sub,
}: {
  label: string;
  value: string;
  unit?: string;
  accent?: string;
  sub?: string;
}) {
  return (
    <div
      className="panel"
      style={{
        padding: "14px 16px",
        borderLeft: accent ? `2px solid ${accent}` : undefined,
      }}
    >
      <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>{label}</div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 5 }}>
        <span className="mono" style={{ fontSize: 24, fontWeight: 600 }}>
          {value}
        </span>
        {unit && (
          <span className="mono" style={{ fontSize: 13, color: "var(--text-tertiary)" }}>
            {unit}
          </span>
        )}
      </div>
      {sub && <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 6 }}>{sub}</div>}
    </div>
  );
}
