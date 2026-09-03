import { useState } from 'react';
import { useExperiment } from '@/data/hooks';
import { Card } from '@/components/common/Card';
import { ComparisonBars } from '@/components/common/ComparisonBars';
import { QosLegend, StackedQosBar } from '@/components/common/StackedQosBar';
import { LineChart, SeriesLegend, tickLabel } from '@/components/common/LineChart';
import { ErrorState, LoadingState } from '@/components/common/States';
import { baselineDelta, formatNumber } from '@/lib/format';
import { useElementSize } from '@/lib/useElementSize';
import type { ComparisonMetric, ExperimentQuery } from '@/types/domain';

const QOS_FILTERS: { value: ExperimentQuery['qos']; label: string }[] = [
  { value: 'all', label: '전체' },
  { value: 0, label: 'QoS 0' },
  { value: 1, label: 'QoS 1' },
  { value: 2, label: 'QoS 2' },
  { value: 'dynamic', label: 'Dynamic' },
];

const RANGES = [10, 30, 60] as const;

export function ExperimentPage() {
  const [qos, setQos] = useState<ExperimentQuery['qos']>('all');
  const [rangeMinutes, setRangeMinutes] = useState<number>(60);
  const { data, isPending, error, refetch } = useExperiment({ qos, rangeMinutes });

  return (
    <main className="page">
      <Card>
        <div className="row" style={{ flexWrap: 'wrap', gap: 12 }}>
          <div className="segmented" role="group" aria-label="QoS 필터">
            {QOS_FILTERS.map((f) => (
              <button key={String(f.value)} type="button" aria-pressed={qos === f.value} onClick={() => setQos(f.value)}>
                {f.label}
              </button>
            ))}
          </div>
          <div className="segmented" role="group" aria-label="구간 선택">
            {RANGES.map((r) => (
              <button key={r} type="button" aria-pressed={rangeMinutes === r} onClick={() => setRangeMinutes(r)}>
                최근 {r}분
              </button>
            ))}
          </div>
          <span className="spacer" />
          <button type="button" className="btn" disabled>
            CSV 내려받기
          </button>
        </div>
      </Card>

      {error ? (
        <ErrorState error={error} onRetry={refetch} />
      ) : isPending || !data ? (
        <LoadingState />
      ) : (
        <>
          <div className="grid grid--4">
            {data.headline.map((m) => (
              <HeadlineCard key={m.key} metric={m} />
            ))}
          </div>

          <div className="grid grid--2">
            <Card
              title="전송 성공률"
              titleEn="Packet Loss Control"
              meta={data.successRate.context}
              footer={<DeltaLine metric={data.successRate} />}
            >
              <ComparisonBars metric={data.successRate} max={100} />
            </Card>

            <Card
              title="누적 소비 전력"
              titleEn="Power Efficiency"
              meta={data.energy.context}
              footer={<DeltaLine metric={data.energy} />}
            >
              <ComparisonBars metric={data.energy} />
            </Card>
          </div>

          <div className="grid grid--2">
            <Card title="QoS 레벨 분포" titleEn="QoS Distribution" actions={<QosLegend />} footer={data.summaryText || undefined}>
              <StackedQosBar rows={data.qosDistribution} />
            </Card>

            <Card title="지연 시간 추이" titleEn="Latency Trend">
              {data.latencyTrend.length < 2 ? (
                <span className="card__meta">지연 시계열이 아직 없습니다.</span>
              ) : (
                <>
                  <SeriesLegend
                    items={[
                      { label: 'Gingerbread', color: 'var(--series-gingerbread)' },
                      { label: 'Legacy MQTT', color: 'var(--series-legacy)' },
                    ]}
                  />
                  <LatencyChart points={data.latencyTrend} />
                </>
              )}
            </Card>
          </div>
        </>
      )}
    </main>
  );
}

function LatencyChart({ points }: { points: { minute: number; gingerbread: number; legacy: number }[] }) {
  const [chartRef, chartSize] = useElementSize();
  const maxMinute = points[points.length - 1].minute;
  const maxValue = Math.max(...points.map((p) => Math.max(p.gingerbread, p.legacy)));
  const yMax = Math.ceil(maxValue / 20) * 20;

  return (
    <div className="chart-fill" ref={chartRef}>
    <LineChart
      ariaLabel="지연 시간 추이 비교"
      height={Math.max(200, chartSize.height)}
      width={chartSize.width}
      xDomain={[0, maxMinute]}
      yDomain={[0, yMax]}
      series={[
        {
          key: 'legacy',
          label: 'Legacy MQTT',
          color: 'var(--series-legacy)',
          points: points.map((p) => ({ x: p.minute, y: p.legacy })),
          endLabel: formatNumber(points[points.length - 1].legacy, 0),
        },
        {
          key: 'gingerbread',
          label: 'Gingerbread',
          color: 'var(--series-gingerbread)',
          points: points.map((p) => ({ x: p.minute, y: p.gingerbread })),
          endLabel: formatNumber(points[points.length - 1].gingerbread, 0),
        },
      ]}
      yTicks={[
        { y: yMax, label: `${tickLabel(yMax)} ms` },
        { y: yMax / 2, label: tickLabel(yMax / 2) },
        { y: 0, label: '0', emphasis: true },
      ]}
      xTicks={[
        { x: 0, label: '0' },
        { x: maxMinute / 2, label: tickLabel(maxMinute / 2) },
        { x: maxMinute, label: `${maxMinute} min` },
      ]}
    />
    </div>
  );
}

function HeadlineCard({ metric }: { metric: ComparisonMetric }) {
  const delta = baselineDelta(metric.gingerbread, metric.unit, {
    value: metric.legacy,
    label: '',
    trend: metric.trend,
  });

  return (
    <article className="card" style={{ gap: 12, padding: '18px 20px' }}>
      <span className="label">{metric.label}</span>
      <span style={{ font: 'var(--text-metric)', letterSpacing: '-0.02em' }}>
        {formatNumber(metric.gingerbread, metric.precision)}
        <span style={{ font: '500 15px/1 var(--font-sans)', color: 'var(--ink-secondary)', marginLeft: 3 }}>{metric.unit}</span>
      </span>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, borderTop: '1px solid var(--line-grid)', paddingTop: 10 }}>
        <span
          style={{
            font: '500 12px/1 var(--font-sans)',
            color: delta.improved ? 'var(--status-positive-ink)' : 'var(--status-critical-ink)',
          }}
        >
          {delta.text}
        </span>
        <span style={{ font: 'var(--text-body)', color: 'var(--ink-secondary)' }}>
          베이스라인 {formatNumber(metric.legacy, metric.precision)} {metric.unit} 대비
        </span>
      </div>
    </article>
  );
}

function DeltaLine({ metric }: { metric: ComparisonMetric }) {
  const delta = baselineDelta(metric.gingerbread, metric.unit, { value: metric.legacy, label: '', trend: metric.trend });
  return (
    <span className="row">
      <strong style={{ color: delta.improved ? 'var(--status-positive-ink)' : 'var(--status-critical-ink)' }}>{delta.text}</strong>
      <span>동일 조건에서의 차이</span>
    </span>
  );
}

