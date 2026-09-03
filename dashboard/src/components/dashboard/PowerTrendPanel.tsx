import type { PowerTrend } from '@/types/domain';
import { Card } from '@/components/common/Card';
import { LineChart, SeriesLegend, tickLabel } from '@/components/common/LineChart';
import { EmptyState } from '@/components/common/States';
import { formatNumber } from '@/lib/format';
import { useElementSize } from '@/lib/useElementSize';

/**
 * "Savings"라는 제목과 우상향 곡선의 모순을 없앴습니다.
 * 누적 소비량을 그대로 그리되 베이스라인 곡선을 함께 놓아,
 * 두 선 사이의 간격이 절감량이 되게 했습니다.
 */
export function PowerTrendPanel({ trend }: { trend: PowerTrend }) {
  const [chartRef, chartSize] = useElementSize();

  if (trend.points.length < 2) {
    return (
      <Card title="누적 소비 전력" titleEn={trend.unit}>
        <EmptyState label="전력 추정치가 아직 없습니다" />
      </Card>
    );
  }

  const last = trend.points[trend.points.length - 1];
  const maxMinute = last.minute;
  const maxValue = Math.max(...trend.points.map((p) => Math.max(p.gingerbread, p.legacy)));
  const yMax = Math.ceil(maxValue / 50) * 50;
  const saved = last.legacy - last.gingerbread;

  return (
    <Card
      title="누적 소비 전력"
      titleEn={trend.unit}
      footer={
        <>
          같은 {maxMinute}분 동안{' '}
          <strong style={{ color: 'var(--status-positive-ink)' }}>{formatNumber(saved, 1)} {trend.unit}</strong> 덜 썼습니다.
        </>
      }
    >
      <SeriesLegend
        items={[
          { label: 'Gingerbread', color: 'var(--series-gingerbread)' },
          { label: 'Legacy MQTT', color: 'var(--series-legacy)' },
        ]}
      />
      <div className="chart-fill" ref={chartRef}>
      <LineChart
        ariaLabel="누적 소비 전력 비교"
        height={Math.max(180, chartSize.height)}
        width={chartSize.width}
        xDomain={[0, maxMinute]}
        yDomain={[0, yMax]}
        series={[
          {
            key: 'legacy',
            label: 'Legacy MQTT',
            color: 'var(--series-legacy)',
            points: trend.points.map((p) => ({ x: p.minute, y: p.legacy })),
            endLabel: formatNumber(last.legacy, 1),
          },
          {
            key: 'gingerbread',
            label: 'Gingerbread',
            color: 'var(--series-gingerbread)',
            points: trend.points.map((p) => ({ x: p.minute, y: p.gingerbread })),
            endLabel: formatNumber(last.gingerbread, 1),
          },
        ]}
        yTicks={[
          { y: yMax, label: tickLabel(yMax) },
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
    </Card>
  );
}
