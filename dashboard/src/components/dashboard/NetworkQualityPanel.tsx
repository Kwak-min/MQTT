import type { GaugeMetric } from '@/types/domain';
import { Card } from '@/components/common/Card';
import { ThresholdMeter } from '@/components/common/ThresholdMeter';

/**
 * 개선 2: 3중 도넛 → 게이지 여러 줄.
 * 지표마다 자기 축·자기 단위를 갖고, 검은 세로선이 임계값입니다.
 * 색이 하나뿐이라 범례가 필요 없어 중복 범례 두 벌이 사라집니다.
 */
export function NetworkQualityPanel({ metrics }: { metrics: GaugeMetric[] }) {
  return (
    <Card
      title="Network Quality"
      titleEn="네트워크 품질"
      meta="최근 60초 평균"
      footer={
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
          <svg width="12" height="14" viewBox="0 0 12 14" aria-hidden>
            <line x1="6" y1="1" x2="6" y2="13" stroke="var(--line-threshold)" strokeWidth="2" />
          </svg>
          검은 세로선 = 임계값
        </span>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
        {metrics.map((m) => (
          <ThresholdMeter key={m.key} metric={m} />
        ))}
      </div>
    </Card>
  );
}
