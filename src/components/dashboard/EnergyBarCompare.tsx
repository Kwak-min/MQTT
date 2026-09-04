import { formatEnergyMwh } from "@/lib/format";

interface EnergyBar {
  label: string;
  color: string;
  valueMj: number;
}

export function EnergyBarCompare({ bars }: { bars: EnergyBar[] }) {
  const max = Math.max(...bars.map((b) => b.valueMj), 1);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {bars.map((bar) => {
        const pct = Math.max((bar.valueMj / max) * 100, 2);
        return (
          <div key={bar.label}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                fontSize: 12,
                marginBottom: 6,
                color: "var(--text-secondary)",
              }}
            >
              <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span className="dot" style={{ background: bar.color }} />
                {bar.label}
              </span>
              <span className="mono" style={{ color: "var(--text-primary)" }}>
                {formatEnergyMwh(bar.valueMj)}
              </span>
            </div>
            <div
              style={{
                height: 10,
                borderRadius: 999,
                background: "var(--bg-inset)",
                overflow: "hidden",
                border: "1px solid var(--border-hair)",
              }}
            >
              <div
                style={{
                  width: `${pct}%`,
                  height: "100%",
                  background: bar.color,
                  borderRadius: 999,
                  transition: "width 300ms ease",
                }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
