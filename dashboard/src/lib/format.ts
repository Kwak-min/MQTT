import type { Baseline, QosLevel, StatusLevel } from '@/types/domain';

export function formatNumber(value: number, precision: number): string {
  return value.toLocaleString('ko-KR', {
    minimumFractionDigits: precision,
    maximumFractionDigits: precision,
  });
}

/** 음수 기호를 U+2212로 바꿔 하이픈보다 또렷하게 보이게 합니다. */
export function formatSigned(value: number, precision: number): string {
  return formatNumber(value, precision).replace(/^-/, '−');
}

export function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString('ko-KR', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export function formatClock(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString('ko-KR', { hour12: false, hour: '2-digit', minute: '2-digit' });
}

export function formatUptime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  return [h, m, s].map((n) => String(n).padStart(2, '0')).join(':');
}

export function relativeTime(iso: string, now = Date.now()): string {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return '';
  const diff = Math.max(0, Math.round((now - t) / 1000));
  if (diff < 5) return '방금';
  if (diff < 60) return `${diff}초 전`;
  if (diff < 3600) return `${Math.round(diff / 60)}분 전`;
  return `${Math.round(diff / 3600)}시간 전`;
}

/** 베이스라인 대비 개선폭. %p인지 %인지 단위에 따라 구분합니다. */
export function baselineDelta(
  value: number,
  unit: string,
  baseline: Baseline,
): { text: string; improved: boolean } {
  const isPercentPoint = unit === '%';
  const raw = isPercentPoint
    ? value - baseline.value
    : ((value - baseline.value) / baseline.value) * 100;
  const improved = baseline.trend === 'higher-is-better' ? raw > 0 : raw < 0;
  const arrow = raw > 0 ? '▲' : '▼';
  const magnitude = Math.abs(raw);
  const suffix = isPercentPoint ? '%p' : '%';
  return { text: `${arrow} ${formatNumber(magnitude, 1)}${suffix}`, improved };
}

export const qosVar = (qos: QosLevel) => `var(--qos-${qos})`;
export const qosInkVar = (qos: QosLevel) => `var(--qos-${qos}-ink)`;

export const statusVar = (level: StatusLevel) =>
  level === 'normal'
    ? 'var(--status-normal)'
    : level === 'warning'
      ? 'var(--status-warning-ink)'
      : level === 'serious'
        ? 'var(--status-serious-ink)'
        : 'var(--status-critical-ink)';

/** 값을 0..1 비율로. 축을 벗어나면 잘라냅니다. */
export function ratio(value: number, min: number, max: number): number {
  if (max === min) return 0;
  return Math.min(1, Math.max(0, (value - min) / (max - min)));
}

export function isThresholdViolated(
  value: number,
  threshold: { value: number; direction: 'max' | 'min' },
): boolean {
  return threshold.direction === 'max' ? value > threshold.value : value < threshold.value;
}
