import type { DeviceState, FirmwareVariant } from "./dataSource/types";

/** 가스 저항: 전송 단위는 Ω, 표시 단위는 kΩ (docs/API-CONTRACT.md 합의 필요 항목) */
export function formatGasResistance(ohm: number): string {
  return `${(ohm / 1000).toFixed(1)} kΩ`;
}

export function formatTemperature(celsius: number): string {
  return `${celsius.toFixed(1)}°C`;
}

export function formatHumidity(pct: number): string {
  return `${pct.toFixed(1)}%`;
}

export function formatPressure(hpa: number): string {
  return `${hpa.toFixed(1)} hPa`;
}

export function formatVoltage(v: number): string {
  return `${v.toFixed(3)} V`;
}

export function formatCurrent(ma: number): string {
  return `${ma.toFixed(1)} mA`;
}

export function formatPower(mw: number): string {
  return `${mw.toFixed(0)} mW`;
}

/** 1회 통신 에너지 표시 (mJ 단위, 소수 1자리) */
export function formatEnergyMj(mj: number): string {
  return `${mj.toFixed(1)} mJ`;
}

/** 누적 에너지 등 큰 값은 mWh 로 환산 (1 mWh = 3600 mJ) */
export function formatEnergyMwh(mj: number): string {
  return `${(mj / 3600).toFixed(2)} mWh`;
}

export function formatUptime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}시간 ${m}분`;
  return `${m}분`;
}

/** ISO 8601 + 오프셋 문자열을 화면용 상대/절대 시간으로 변환 */
export function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

export function formatRelativeTime(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const diffS = Math.round(diffMs / 1000);
  if (diffS < 5) return "방금";
  if (diffS < 60) return `${diffS}초 전`;
  const diffM = Math.round(diffS / 60);
  if (diffM < 60) return `${diffM}분 전`;
  const diffH = Math.round(diffM / 60);
  if (diffH < 24) return `${diffH}시간 전`;
  return `${Math.round(diffH / 24)}일 전`;
}

export const FIRMWARE_LABEL: Record<FirmwareVariant, string> = {
  standard_mqtt: "Standard MQTT",
  monitor: "Monitor",
  gingerbread: "Gingerbread",
};

export const FIRMWARE_COLOR: Record<FirmwareVariant, string> = {
  standard_mqtt: "var(--signal-teal)",
  monitor: "var(--signal-amber)",
  gingerbread: "var(--signal-violet)",
};

export const DEVICE_STATE_LABEL: Record<DeviceState, string> = {
  active: "ACTIVE",
  asleep: "ASLEEP",
};

export const DEVICE_STATE_COLOR: Record<DeviceState, string> = {
  active: "var(--signal-green)",
  asleep: "var(--text-tertiary)",
};
