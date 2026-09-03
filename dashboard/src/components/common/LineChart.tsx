import { useId } from 'react';
import { formatNumber, ratio } from '@/lib/format';

export interface LineSeries {
  key: string;
  label: string;
  color: string;
  points: { x: number; y: number }[];
  /** 마지막 점 옆에 값을 직접 표기 (범례 의존을 줄입니다) */
  endLabel?: string;
}

interface Props {
  series: LineSeries[];
  xTicks: { x: number; label: string }[];
  yTicks: { y: number; label: string; emphasis?: boolean }[];
  xDomain: [number, number];
  yDomain: [number, number];
  height?: number;
  /** 좌표계 너비. 컨테이너 실측값을 넘기면 확대·축소 없이 1:1로 그립니다. */
  width?: number;
  /** 임계값 가로 점선 */
  thresholdY?: number;
  ariaLabel: string;
}

const PAD = { left: 40, right: 56, top: 12, bottom: 22 };
const DEFAULT_VIEW_W = 860;

/**
 * 축이 하나뿐인 선 차트. 단위가 다른 두 지표를 한 그림에 겹치지 않습니다
 * (이중 Y축 금지 — 필요하면 차트를 나눕니다).
 */
export function LineChart({ series, xTicks, yTicks, xDomain, yDomain, height = 150, width, thresholdY, ariaLabel }: Props) {
  const id = useId();
  const viewW = width && width > 200 ? width : DEFAULT_VIEW_W;
  const innerW = viewW - PAD.left - PAD.right;
  const innerH = height - PAD.top - PAD.bottom;

  const px = (x: number) => PAD.left + ratio(x, xDomain[0], xDomain[1]) * innerW;
  const py = (y: number) => PAD.top + (1 - ratio(y, yDomain[0], yDomain[1])) * innerH;

  return (
    <svg width="100%" height="100%" viewBox={`0 0 ${viewW} ${height}`} fill="none" role="img" aria-label={ariaLabel}>
      {yTicks.map((t) => (
        <g key={`${id}-y-${t.y}`}>
          <line
            x1={PAD.left}
            y1={py(t.y)}
            x2={viewW - PAD.right}
            y2={py(t.y)}
            stroke={t.emphasis ? 'var(--line-axis)' : 'var(--line-grid)'}
            strokeWidth="1"
          />
          <text
            x={PAD.left - 8}
            y={py(t.y) + 4}
            textAnchor="end"
            fontFamily="var(--font-mono)"
            fontSize="10"
            fill="var(--ink-muted)"
          >
            {t.label}
          </text>
        </g>
      ))}

      {thresholdY !== undefined && thresholdY >= yDomain[0] && thresholdY <= yDomain[1] && (
        <line
          x1={PAD.left}
          y1={py(thresholdY)}
          x2={viewW - PAD.right}
          y2={py(thresholdY)}
          stroke="var(--status-critical-ink)"
          strokeWidth="1.2"
          strokeDasharray="5 4"
        />
      )}

      {series.map((s) => {
        if (s.points.length === 0) return null;
        const d = s.points.map((p) => `${px(p.x).toFixed(1)},${py(p.y).toFixed(1)}`).join(' ');
        const last = s.points[s.points.length - 1];
        return (
          <g key={s.key}>
            <polyline points={d} stroke={s.color} strokeWidth="2" fill="none" strokeLinejoin="round" strokeLinecap="round" />
            <circle cx={px(last.x)} cy={py(last.y)} r="3.5" fill={s.color} stroke="var(--surface-card)" strokeWidth="2" />
            {s.endLabel && (
              <text
                x={px(last.x) + 10}
                y={py(last.y) + 4}
                fontFamily="var(--font-mono)"
                fontSize="11.5"
                fontWeight="600"
                fill="var(--ink-primary)"
              >
                {s.endLabel}
              </text>
            )}
          </g>
        );
      })}

      {xTicks.map((t) => (
        <text
          key={`${id}-x-${t.x}`}
          x={px(t.x)}
          y={height - 5}
          textAnchor="middle"
          fontFamily="var(--font-mono)"
          fontSize="10"
          fill="var(--ink-muted)"
        >
          {t.label}
        </text>
      ))}
    </svg>
  );
}

export function niceTicks(min: number, max: number, count = 3): number[] {
  if (count < 2) return [min, max];
  const step = (max - min) / (count - 1);
  return Array.from({ length: count }, (_, i) => min + step * i);
}

export function tickLabel(value: number, precision = 0): string {
  return formatNumber(value, precision);
}

export function SeriesLegend({ items }: { items: { label: string; color: string }[] }) {
  return (
    <div style={{ display: 'flex', gap: 14 }}>
      {items.map((it) => (
        <span key={it.label} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 10, height: 3, borderRadius: 2, background: it.color }} aria-hidden />
          <span style={{ font: 'var(--text-caption)', color: 'var(--ink-secondary)' }}>{it.label}</span>
        </span>
      ))}
    </div>
  );
}
