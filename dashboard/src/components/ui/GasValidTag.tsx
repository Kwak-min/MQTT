export function GasValidTag({ valid }: { valid: boolean | undefined }) {
  if (valid === undefined) {
    return (
      <span className="tag" style={{ color: "var(--text-tertiary)" }}>
        <span className="dot" style={{ background: "var(--text-tertiary)" }} />
        gas_valid 없음
      </span>
    );
  }
  const color = valid ? "var(--signal-green)" : "var(--signal-red)";
  return (
    <span className="tag mono" style={{ borderColor: color, color }}>
      <span className="dot" style={{ background: color }} />
      gas_valid {valid ? "true" : "false"}
    </span>
  );
}
