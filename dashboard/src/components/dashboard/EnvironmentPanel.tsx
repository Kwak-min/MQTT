import type { EnvironmentSeries } from '@/types/domain';
import { Card } from '@/components/common/Card';
import { LineChart, tickLabel } from '@/components/common/LineChart';
import { EmptyState } from '@/components/common/States';
import { formatClock, formatNumber } from '@/lib/format';

/**
 * 가스 저항(kΩ)과 온도(°C)는 스케일이 다르므로 이중 Y축 대신
 * 같은 X축을 공유하는 두 개의 작은 차트로 나눠 그립니다.
 */
export function EnvironmentPanel({ series }: { series: EnvironmentSeries[] }) {
  const hasData = series.some((s) => s.points.length > 1);

  return (
    <Card title="Environmental Sensors" titleEn="환경 센서" meta="노드 전송 주기 기준">
      {!hasData ? (
        <EmptyState label="센서 패킷이 아직 도착하지 않았습니다" />
      ) : (
        series.map((s, index) => {
          const xs = s.points.map((_, i) => i);
          const xDomain: [number, number] = [0, Math.max(1, xs.length - 1)];
          const yDomain: [number, number] = [s.axis.min, s.axis.max];
          const last = s.points[s.points.length - 1];
          const isLast = index === series.length - 1;

          const xTicks = isLast
            ? [0, Math.floor((s.points.length - 1) / 3), Math.floor((2 * (s.points.length - 1)) / 3), s.points.length - 1].map(
                (i) => ({ x: i, label: formatClock(s.points[i].t) }),
              )
            : [];

          return (
            <div key={s.key} style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                <span style={{ font: 'var(--text-body-strong)', color: 'var(--ink-secondary)' }}>{s.label}</span>
                <span style={{ font: 'var(--text-caption)', color: 'var(--ink-muted)' }}>{s.unit}</span>
                <span className="spacer" />
                <span style={{ font: '600 15px/1 var(--font-sans)' }}>{formatNumber(last.value, s.precision)}</span>
                {s.threshold ? (
                  <span style={{ font: 'var(--text-caption)', color: 'var(--ink-muted)' }}>{s.threshold.label}</span>
                ) : null}
              </div>
              <LineChart
                ariaLabel={`${s.label} 추이`}
                height={100}
                series={[
                  {
                    key: s.key,
                    label: s.label,
                    color: 'var(--series-gingerbread)',
                    points: s.points.map((p, i) => ({ x: i, y: p.value })),
                  },
                ]}
                xDomain={xDomain}
                yDomain={yDomain}
                thresholdY={s.threshold?.value}
                xTicks={xTicks}
                yTicks={[
                  { y: s.axis.max, label: tickLabel(s.axis.max, 0) },
                  { y: (s.axis.max + s.axis.min) / 2, label: tickLabel((s.axis.max + s.axis.min) / 2, 0) },
                  { y: s.axis.min, label: tickLabel(s.axis.min, 0) },
                ]}
              />
            </div>
          );
        })
      )}
    </Card>
  );
}
