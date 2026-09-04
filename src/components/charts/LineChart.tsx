interface Series {
  label: string;
  color: string;
  values: number[];
}

export function LineChart({
  series,
  height = 180,
  formatValue,
}: {
  series: Series[];
  height?: number;
  formatValue?: (v: number) => string;
}) {
  const width = 100; // viewBox 단위, 실제 렌더링은 컨테이너 폭에 맞춰 스케일
  const padY = 10;

  const allValues = series.flatMap((s) => s.values);
  if (allValues.length === 0) {
    return <EmptyChart height={height} />;
  }

  const min = Math.min(...allValues);
  const max = Math.max(...allValues);
  const span = max - min || 1;

  const toPoints = (values: number[]) =>
    values
      .map((v, i) => {
        const x = (i / Math.max(values.length - 1, 1)) * width;
        const y = padY + (1 - (v - min) / span) * (100 - padY * 2);
        return `${x},${y}`;
      })
      .join(" ");

  return (
    <div>
      <svg
        viewBox={`0 0 ${width} 100`}
        preserveAspectRatio="none"
        style={{ width: "100%", height, display: "block" }}
      >
        <line x1="0" y1="50" x2={width} y2="50" stroke="var(--border-hair)" strokeWidth="0.3" />
        {series.map((s) => (
          <polyline
            key={s.label}
            points={toPoints(s.values)}
            fill="none"
            stroke={s.color}
            strokeWidth="1"
            vectorEffect="non-scaling-stroke"
          />
        ))}
      </svg>
      <div style={{ display: "flex", gap: 16, marginTop: 10, flexWrap: "wrap" }}>
        {series.map((s) => {
          const last = s.values[s.values.length - 1];
          return (
            <div key={s.label} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
              <span className="dot" style={{ background: s.color }} />
              <span style={{ color: "var(--text-secondary)" }}>{s.label}</span>
              {last !== undefined && (
                <span className="mono" style={{ color: "var(--text-primary)" }}>
                  {formatValue ? formatValue(last) : last.toFixed(1)}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function EmptyChart({ height }: { height: number }) {
  return (
    <div
      style={{
        height,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: "var(--text-tertiary)",
        fontSize: 12,
      }}
    >
      데이터를 기다리는 중…
    </div>
  );
}
