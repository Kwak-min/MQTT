import type { KpiMetric } from '@/types/domain';
import { KpiCard } from './KpiCard';

/** 개선 1: 수치 4장만 카드로. 상태는 위 StatusStrip이 담당합니다. */
export function KpiRow({ metrics }: { metrics: KpiMetric[] }) {
  return (
    <div className="grid grid--4">
      {metrics.map((m) => (
        <KpiCard key={m.key} metric={m} />
      ))}
    </div>
  );
}
