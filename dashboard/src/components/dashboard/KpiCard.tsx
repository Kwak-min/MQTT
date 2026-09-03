import type { KpiMetric } from '@/types/domain';
import { LevelBar, Sparkline } from '@/components/common/Sparkline';
import { StatusIcon } from '@/components/common/StatusIcon';
import { baselineDelta, formatSigned } from '@/lib/format';

/**
 * 개선 1: 수치 카드에는 값 + 스파크라인 + 베이스라인 대비 증감을 함께 둡니다.
 * "지금 값"이 아니라 "어디로 가는 중인지"와 "베이스라인보다 나은지"가 같이 보입니다.
 */
export function KpiCard({ metric }: { metric: KpiMetric }) {
  const delta = metric.baseline ? baselineDelta(metric.value, metric.unit, metric.baseline) : null;

  return (
    <article className="card" style={{ gap: 12, padding: '18px 20px' }}>
      <span className="label">{metric.label}</span>

      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 10 }}>
        <span style={{ font: 'var(--text-metric)', letterSpacing: '-0.02em' }}>
          {formatSigned(metric.value, metric.precision)}
          <span style={{ font: '500 15px/1 var(--font-sans)', color: 'var(--ink-secondary)', marginLeft: 3 }}>
            {metric.unit}
          </span>
        </span>
        {metric.sparkKind === 'bar' ? (
          <LevelBar value={metric.value} />
        ) : (
          <Sparkline values={metric.history} threshold={metric.threshold?.value} />
        )}
      </div>

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          borderTop: '1px solid var(--line-grid)',
          paddingTop: 10,
          minHeight: 30,
        }}
      >
        {delta && metric.baseline ? (
          <>
            <span
              style={{
                font: '500 12px/1 var(--font-sans)',
                color: delta.improved ? 'var(--status-positive-ink)' : 'var(--status-critical-ink)',
              }}
            >
              {delta.text}
            </span>
            <span style={{ font: 'var(--text-body)', color: 'var(--ink-secondary)' }}>{metric.baseline.label}</span>
          </>
        ) : metric.threshold && metric.status !== 'normal' ? (
          <>
            <StatusIcon level={metric.status} />
            <span style={{ font: 'var(--text-body)', color: 'var(--ink-secondary)' }}>{metric.threshold.label}</span>
          </>
        ) : (
          <span style={{ font: 'var(--text-body)', color: 'var(--ink-secondary)' }}>{metric.note ?? ''}</span>
        )}
      </div>
    </article>
  );
}
