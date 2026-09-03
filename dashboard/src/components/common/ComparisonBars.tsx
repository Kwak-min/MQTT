import type { ComparisonMetric } from '@/types/domain';
import { formatNumber } from '@/lib/format';

/**
 * 이중 아크 도넛을 대체하는 막대 2개.
 * 같은 0 기준선에서 시작하므로 길이 차이가 곧 성능 차이입니다.
 */
export function ComparisonBars({ metric, max }: { metric: ComparisonMetric; max?: number }) {
  const top = max ?? Math.max(metric.gingerbread, metric.legacy) * 1.05;
  const rows = [
    { label: 'Gingerbread', value: metric.gingerbread, color: 'var(--series-gingerbread)' },
    { label: 'Legacy MQTT', value: metric.legacy, color: 'var(--series-legacy)' },
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {rows.map((row) => (
        <div key={row.label} style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
            <span style={{ width: 9, height: 9, borderRadius: 2, background: row.color }} aria-hidden />
            <span style={{ font: 'var(--text-body-strong)' }}>{row.label}</span>
          </span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div style={{ flexGrow: 1, height: 28, background: 'var(--surface-subtle)', borderRadius: 4 }}>
              <div
                style={{
                  width: `${Math.max(0, Math.min(1, row.value / top)) * 100}%`,
                  height: 28,
                  borderRadius: 4,
                  background: row.color,
                }}
              />
            </div>
            <span style={{ font: '600 20px/1 var(--font-sans)', width: 86, textAlign: 'right' }}>
              {formatNumber(row.value, metric.precision)}
              <span style={{ font: 'var(--text-caption)', color: 'var(--ink-secondary)', marginLeft: 2 }}>{metric.unit}</span>
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}
