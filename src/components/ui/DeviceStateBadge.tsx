import { DEVICE_STATE_COLOR, DEVICE_STATE_LABEL } from "@/lib/format";
import type { DeviceState } from "@/lib/dataSource/types";

export function DeviceStateBadge({ state }: { state: DeviceState | undefined }) {
  if (!state) {
    return (
      <span className="tag" style={{ color: "var(--text-tertiary)" }}>
        <span className="dot" style={{ background: "var(--text-tertiary)" }} />
        해당 없음
      </span>
    );
  }
  const color = DEVICE_STATE_COLOR[state];
  return (
    <span className="tag mono" style={{ borderColor: color, color }}>
      <span className={state === "active" ? "dot dot-pulse" : "dot"} style={{ background: color }} />
      {DEVICE_STATE_LABEL[state]}
    </span>
  );
}
