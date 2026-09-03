import { useOperations } from '@/data/OperationsContext';
import { StatusStrip } from '@/components/dashboard/StatusStrip';
import { KpiRow } from '@/components/dashboard/KpiRow';
import { EnvironmentPanel } from '@/components/dashboard/EnvironmentPanel';
import { NetworkQualityPanel } from '@/components/dashboard/NetworkQualityPanel';
import { PowerTrendPanel } from '@/components/dashboard/PowerTrendPanel';
import { AiDecisionPanel } from '@/components/dashboard/AiDecisionPanel';
import { DeviceConfigPanel } from '@/components/dashboard/DeviceConfigPanel';
import { PacketLogPanel } from '@/components/dashboard/PacketLogPanel';
import { ErrorState, LoadingState } from '@/components/common/States';

export function OperationsPage() {
  const { data, isPending, error, refetch } = useOperations();

  if (error) return <main className="page"><ErrorState error={error} onRetry={refetch} /></main>;
  if (isPending || !data) return <main className="page"><LoadingState /></main>;

  return (
    <main className="page">
      <StatusStrip node={data.node} />
      <KpiRow metrics={data.kpis} />

      <div className="grid grid--main">
        <div className="stack">
          <EnvironmentPanel series={data.environment} />
          <div className="grid grid--2">
            <NetworkQualityPanel metrics={data.networkQuality} />
            <PowerTrendPanel trend={data.powerTrend} />
          </div>
        </div>
        <div className="stack">
          <AiDecisionPanel decision={data.aiDecision} />
          <DeviceConfigPanel entries={data.config} />
        </div>
      </div>

      <PacketLogPanel rows={data.packetLog} />
    </main>
  );
}
