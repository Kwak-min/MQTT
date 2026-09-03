import type { NodeStatus } from '@/types/domain';
import { StatusPill } from '@/components/common/StatusIcon';
import { formatUptime, qosInkVar, qosVar, relativeTime } from '@/lib/format';

/**
 * 개선 1: 상태 3종(네트워크·긴급도·현재 QoS)을 KPI 카드에서 빼내
 * 얇은 스트립으로 올렸습니다. 수치와 상태가 같은 무게로 경쟁하지 않습니다.
 */
export function StatusStrip({ node }: { node: NodeStatus }) {
  return (
    <section
      className="card"
      style={{ flexDirection: 'row', alignItems: 'center', gap: 20, padding: '13px 20px', flexWrap: 'wrap' }}
    >
      <span className="label">
        {node.nodeName} · {node.hardware} · {node.ip}
      </span>
      <span style={{ width: 1, height: 18, background: 'var(--line-grid)' }} aria-hidden />

      <StatusPill level={node.network.level} label={node.network.label} />
      <StatusPill level={node.urgency.level} label={node.urgency.label} detail={node.urgency.detail} />

      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
        <span style={{ font: 'var(--text-body)', color: 'var(--ink-secondary)' }}>현재 QoS</span>
        <span className="qos-chip" style={{ background: qosVar(node.currentQos), color: qosInkVar(node.currentQos) }}>
          {node.currentQos}
        </span>
      </span>

      <span className="spacer" />

      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
        <span
          style={{
            width: 7,
            height: 7,
            borderRadius: '50%',
            background: node.gatewayConnected ? 'var(--status-normal)' : 'var(--status-critical)',
          }}
          aria-hidden
        />
        <span style={{ font: 'var(--text-body)', color: 'var(--ink-secondary)' }}>
          {node.gatewayConnected ? '게이트웨이 연결됨' : '게이트웨이 끊김'}
        </span>
        <span style={{ font: 'var(--text-body)', color: 'var(--ink-muted)' }}>· {relativeTime(node.updatedAt)} 갱신</span>
      </span>
      <span style={{ font: 'var(--text-body)', color: 'var(--ink-muted)' }}>
        세션 <span className="mono">{node.sessionId}</span> · 가동 {formatUptime(node.uptimeSeconds)}
      </span>
    </section>
  );
}
