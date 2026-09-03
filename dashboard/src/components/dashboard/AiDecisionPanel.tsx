import type { AiDecision } from '@/types/domain';
import { Card } from '@/components/common/Card';
import { formatNumber, formatSigned, formatTime, qosInkVar, qosVar, ratio } from '@/lib/format';

const Arrow = () => (
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" style={{ alignSelf: 'center' }} aria-hidden>
    <path d="M8 2v12M3.5 9.5 8 14l4.5-4.5" stroke="var(--line-axis)" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

/**
 * 이 프로젝트의 핵심 기여를 문단 텍스트가 아니라
 * 입력 → 추론 점수 → 선택된 QoS 의 3단계 흐름으로 보여줍니다.
 */
export function AiDecisionPanel({ decision }: { decision: AiDecision }) {
  const scorePct = ratio(decision.score, 0, 1) * 100;
  const band1 = decision.bands.qos1 * 100;
  const band2 = decision.bands.qos2 * 100;

  return (
    <Card title="AI QoS Decision" titleEn="판단 근거" style={{ gap: 16 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <span className="label">1 · 입력</span>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 8 }}>
          {decision.inputs.map((input) => (
            <div
              key={input.label}
              style={{
                border: '1px solid var(--line-grid)',
                borderRadius: 6,
                padding: '9px 11px',
                display: 'flex',
                flexDirection: 'column',
                gap: 2,
              }}
            >
              <span style={{ font: 'var(--text-caption)', color: 'var(--ink-muted)' }}>{input.label}</span>
              <span style={{ font: '600 13.5px/1.2 var(--font-sans)' }}>
                {formatSigned(input.value, input.precision)} {input.unit}
              </span>
            </div>
          ))}
        </div>
      </div>

      <Arrow />

      <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
        <span className="label">2 · 추론 점수</span>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
          <span style={{ font: '600 26px/1 var(--font-sans)' }}>{formatNumber(decision.score, 3)}</span>
          <span style={{ font: 'var(--text-body)', color: 'var(--ink-secondary)' }}>위험 확률</span>
        </div>
        <div style={{ position: 'relative', height: 26 }}>
          <div style={{ position: 'absolute', insetInline: 0, top: 8, height: 10, borderRadius: 999, background: 'var(--surface-track)' }}>
            <div style={{ width: `${scorePct}%`, height: 10, borderRadius: 999, background: 'var(--series-gingerbread)' }} />
          </div>
          {[band1, band2].map((b) => (
            <div key={b} style={{ position: 'absolute', left: `${b}%`, top: 4, width: 2, height: 18, background: 'var(--line-threshold)' }} aria-hidden />
          ))}
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
          <span style={{ font: 'var(--text-caption)', color: 'var(--ink-muted)' }}>0.00</span>
          <span style={{ font: 'var(--text-caption)', color: 'var(--ink-secondary)' }}>
            {formatNumber(decision.bands.qos1, 2)} · QoS 1
          </span>
          <span style={{ font: 'var(--text-caption)', color: 'var(--ink-secondary)' }}>
            {formatNumber(decision.bands.qos2, 2)} · QoS 2
          </span>
          <span style={{ font: 'var(--text-caption)', color: 'var(--ink-muted)' }}>1.00</span>
        </div>
      </div>

      <Arrow />

      <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
        <span className="label">3 · 선택된 QoS</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span
            style={{
              background: qosVar(decision.selectedQos),
              color: qosInkVar(decision.selectedQos),
              font: '600 24px/1.1 var(--font-sans)',
              padding: '8px 20px',
              borderRadius: 8,
            }}
          >
            QoS {decision.selectedQos}
          </span>
          <span style={{ font: 'var(--text-body)', color: 'var(--ink-secondary)' }}>{decision.reason}</span>
        </div>
      </div>

      {decision.lastSwitch && (
        <div style={{ borderTop: '1px solid var(--line-grid)', paddingTop: 12, display: 'flex', flexDirection: 'column', gap: 7 }}>
          <span className="label">최근 전환</span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            <span className="mono" style={{ font: 'var(--text-caption)', color: 'var(--ink-muted)' }}>
              {formatTime(decision.lastSwitch.at)}
            </span>
            <span className="qos-chip" style={{ background: qosVar(decision.lastSwitch.from), color: qosInkVar(decision.lastSwitch.from) }}>
              {decision.lastSwitch.from}
            </span>
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden>
              <path d="M2 8h11M9.5 4.5 13 8l-3.5 3.5" stroke="var(--ink-muted)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <span className="qos-chip" style={{ background: qosVar(decision.lastSwitch.to), color: qosInkVar(decision.lastSwitch.to) }}>
              {decision.lastSwitch.to}
            </span>
            <span style={{ font: 'var(--text-body)', color: 'var(--ink-secondary)' }}>{decision.lastSwitch.cause}</span>
          </span>
        </div>
      )}
    </Card>
  );
}
