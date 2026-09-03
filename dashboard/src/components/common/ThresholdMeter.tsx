import type { GaugeMetric } from '@/types/domain';
import { formatSigned, isThresholdViolated, ratio } from '@/lib/format';
import { StatusIcon } from './StatusIcon';

/**
 * 단일 축 게이지. 도넛을 대체합니다.
 *  - 각 지표가 자기 축(scale)과 자기 단위를 가짐
 *  - 검은 세로선 = 임계값
 *  - 색이 한 가지뿐이라 범례가 필요 없음
 */
export function ThresholdMeter({ metric, compact = false }: { metric: GaugeMetric; compact?: boolean }) {
  const fill = ratio(metric.value, metric.scale.min, metric.scale.max) * 100;
  const mark = ratio(metric.threshold.value, metric.scale.min, metric.scale.max) * 100;
  const violated = isThresholdViolated(metric.value, metric.threshold);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 8 }}>
        <span style={{ font: 'var(--text-body-strong)' }}>
          {metric.label}
          {!compact && <span style={{ color: 'var(--ink-muted)', fontWeight: 400 }}> {metric.labelEn}</span>}
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          {violated && <StatusIcon level={metric.status} size={14} />}
          <span style={{ font: '600 17px/1 var(--font-sans)' }}>
            {formatSigned(metric.value, metric.precision)}
            <span style={{ font: 'var(--text-caption)', color: 'var(--ink-secondary)', marginLeft: 2 }}>{metric.unit}</span>
          </span>
        </span>
      </div>

      <div style={{ position: 'relative', height: compact ? 15 : 33 }}>
        {!compact && (
          <span
            style={{
              position: 'absolute',
              left: `${mark}%`,
              top: 0,
              transform: 'translateX(-50%)',
              font: 'var(--text-caption)',
              color: 'var(--ink-secondary)',
              whiteSpace: 'nowrap',
            }}
          >
            {metric.threshold.label}
          </span>
        )}
        <div
          style={{
            position: 'absolute',
            insetInline: 0,
            top: compact ? 2 : 18,
            height: compact ? 10 : 11,
            borderRadius: 999,
            background: 'var(--surface-track)',
          }}
        >
          <div
            style={{
              width: `${fill}%`,
              height: '100%',
              borderRadius: 999,
              background: 'var(--series-gingerbread)',
            }}
          />
        </div>
        <div
          style={{
            position: 'absolute',
            left: `${mark}%`,
            top: compact ? 0 : 14,
            width: 2,
            height: compact ? 15 : 19,
            background: 'var(--line-threshold)',
          }}
          aria-hidden
        />
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
        <span style={{ font: 'var(--text-caption)', color: 'var(--ink-muted)' }}>
          {formatSigned(metric.scale.min, 0)}
        </span>
        <span style={{ font: 'var(--text-caption)', color: 'var(--ink-muted)' }}>
          {formatSigned(metric.scale.max, 0)} {metric.unit}
        </span>
      </div>
    </div>
  );
}
