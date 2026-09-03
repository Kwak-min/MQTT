import type { QosDistributionRow } from '@/types/domain';
import { formatNumber, qosInkVar, qosVar } from '@/lib/format';

/**
 * 파이 2개를 대체하는 누적 가로 막대 2줄.
 * 같은 길이·같은 시작점이라 두 시스템의 분포를 겹쳐 읽을 수 있고,
 * QoS 0·1·2는 파랑 한 색의 명도 3단계라 "레벨이 올라간다"가 색으로 읽힙니다.
 */
export function StackedQosBar({ rows }: { rows: QosDistributionRow[] }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {rows.map((row) => (
        <div key={row.system} style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
            <span
              style={{
                width: 9,
                height: 9,
                borderRadius: 2,
                background: row.system === 'gingerbread' ? 'var(--series-gingerbread)' : 'var(--series-legacy)',
              }}
              aria-hidden
            />
            <span style={{ font: 'var(--text-body-strong)' }}>{row.label}</span>
            {row.note ? <span style={{ font: 'var(--text-caption)', color: 'var(--ink-muted)' }}>{row.note}</span> : null}
          </span>
          <div style={{ display: 'flex', gap: 2, height: 34 }}>
            {row.segments.map((seg) => (
              <div
                key={seg.qos}
                style={{
                  flexGrow: seg.share,
                  flexBasis: 0,
                  background: qosVar(seg.qos),
                  color: qosInkVar(seg.qos),
                  borderRadius: 4,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: seg.share > 15 ? 'flex-start' : 'center',
                  paddingLeft: seg.share > 15 ? 12 : 0,
                  font: '600 12.5px/1 var(--font-sans)',
                  overflow: 'hidden',
                  whiteSpace: 'nowrap',
                }}
                title={`QoS ${seg.qos} · ${formatNumber(seg.share, 0)}%`}
              >
                {seg.share >= 8 ? `${formatNumber(seg.share, 0)}%` : ''}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

export function QosLegend() {
  return (
    <div style={{ display: 'flex', gap: 14 }}>
      {([0, 1, 2] as const).map((q) => (
        <span key={q} style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
          <span style={{ width: 9, height: 9, borderRadius: 2, background: qosVar(q) }} aria-hidden />
          <span style={{ font: 'var(--text-caption)', color: 'var(--ink-secondary)' }}>QoS {q}</span>
        </span>
      ))}
    </div>
  );
}
