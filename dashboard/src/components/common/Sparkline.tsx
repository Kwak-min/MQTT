import { ratio } from '@/lib/format';

interface Props {
  values: number[];
  width?: number;
  height?: number;
  /** 임계선을 점선으로 겹쳐 그립니다. */
  threshold?: number;
  color?: string;
}

/** KPI 카드용 초소형 추세선. 축·라벨 없이 방향만 전달합니다. */
export function Sparkline({ values, width = 92, height = 30, threshold, color = 'var(--series-gingerbread)' }: Props) {
  if (values.length < 2) return <svg width={width} height={height} aria-hidden />;

  const min = Math.min(...values, ...(threshold !== undefined ? [threshold] : []));
  const max = Math.max(...values, ...(threshold !== undefined ? [threshold] : []));
  const pad = 4;
  const toY = (v: number) => height - pad - ratio(v, min, max) * (height - pad * 2);
  const toX = (i: number) => (i / (values.length - 1)) * width;

  const points = values.map((v, i) => `${toX(i).toFixed(1)},${toY(v).toFixed(1)}`).join(' ');
  const last = values[values.length - 1];

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} fill="none" aria-hidden>
      {threshold !== undefined && (
        <line x1={0} y1={toY(threshold)} x2={width} y2={toY(threshold)} stroke="var(--line-grid)" strokeWidth="1" strokeDasharray="3 3" />
      )}
      <polyline points={points} stroke={color} strokeWidth="2" fill="none" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={width} cy={toY(last)} r="3" fill={color} stroke="var(--surface-card)" strokeWidth="2" />
    </svg>
  );
}

/** 잔량형 지표(배터리)용. 추세보다 채움 비율이 의미 있는 경우. */
export function LevelBar({ value, max = 100, width = 92, height = 30 }: { value: number; max?: number; width?: number; height?: number }) {
  const w = Math.max(0, Math.min(1, value / max)) * width;
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} fill="none" aria-hidden>
      <rect x={0} y={height / 2 - 6} width={width} height={12} rx={3} fill="var(--line-grid)" />
      <rect x={0} y={height / 2 - 6} width={w} height={12} rx={3} fill="var(--series-gingerbread)" />
    </svg>
  );
}
