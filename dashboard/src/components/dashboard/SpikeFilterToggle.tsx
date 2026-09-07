export function SpikeFilterToggle({
  checked,
  onChange,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <button
      type="button"
      className="btn"
      onClick={() => onChange(!checked)}
      style={{
        borderColor: checked ? "var(--signal-teal)" : "var(--border-hair-strong)",
        background: checked ? "var(--bg-panel-raised)" : "var(--bg-inset)",
      }}
      title="초기 Wi-Fi 연결로 발생하는 이상값(에너지 급증)을 그래프에서 제외합니다"
    >
      <span
        className="dot"
        style={{ background: checked ? "var(--signal-teal)" : "var(--text-tertiary)" }}
      />
      <span style={{ fontSize: 12 }}>스파이크 필터 {checked ? "켜짐" : "꺼짐"}</span>
    </button>
  );
}
