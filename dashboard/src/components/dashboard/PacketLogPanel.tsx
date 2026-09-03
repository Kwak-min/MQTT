import type { PacketLogRow } from '@/types/domain';
import { Card } from '@/components/common/Card';
import { StatusIcon } from '@/components/common/StatusIcon';
import { EmptyState } from '@/components/common/States';
import { formatNumber, formatTime, qosInkVar, qosVar } from '@/lib/format';

const COLUMNS = '96px 88px 108px 56px 96px 108px 96px 68px minmax(0, 1fr)';

/** 이벤트 종류마다 아이콘 + 라벨을 붙여 색 없이도 상태가 읽히게 했습니다. */
export function PacketLogPanel({ rows }: { rows: PacketLogRow[] }) {
  return (
    <Card
      title="System Event & Packet Log"
      titleEn="이벤트 및 패킷 로그"
      actions={
        <div className="row">
          <button type="button" className="btn" disabled>
            전체 QoS
          </button>
          <button type="button" className="btn" disabled>
            최근 10분
          </button>
          <button type="button" className="btn" disabled>
            CSV 내려받기
          </button>
        </div>
      }
    >
      {rows.length === 0 ? (
        <EmptyState label="수신된 패킷이 없습니다" />
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <div style={{ minWidth: 900 }}>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: COLUMNS,
                gap: 12,
                padding: '0 8px 9px',
                borderBottom: '1px solid var(--line-grid)',
              }}
            >
              {['시각', 'Msg ID', '타입', 'QoS', '긴급도', '네트워크', '결과', '재전송', 'RTT'].map((h) => (
                <span key={h} className="label" style={{ letterSpacing: '0.06em' }}>
                  {h}
                </span>
              ))}
            </div>
            {rows.map((row, i) => (
              <div
                key={row.id}
                style={{
                  display: 'grid',
                  gridTemplateColumns: COLUMNS,
                  gap: 12,
                  padding: '10px 8px',
                  alignItems: 'center',
                  borderBottom: i === rows.length - 1 ? 'none' : '1px solid var(--line-hairline)',
                }}
              >
                <span className="mono" style={{ font: '400 12px/1.4 var(--font-mono)', color: 'var(--ink-secondary)' }}>
                  {formatTime(row.time)}
                </span>
                <span className="mono" style={{ font: '400 12px/1.4 var(--font-mono)' }}>
                  {row.msgId}
                </span>
                <span style={{ font: 'var(--text-body)' }}>{row.type}</span>
                <span
                  className="qos-chip"
                  style={{ background: qosVar(row.qos), color: qosInkVar(row.qos), justifySelf: 'start' }}
                >
                  {row.qos}
                </span>
                <LogStatus level={row.urgency.level} label={row.urgency.label} />
                <LogStatus level={row.network.level} label={row.network.label} />
                <LogStatus level={row.result.level} label={row.result.label} />
                <span className="mono" style={{ font: '400 12px/1.4 var(--font-mono)', color: 'var(--ink-secondary)' }}>
                  {row.retry}
                </span>
                <span className="mono" style={{ font: '400 12px/1.4 var(--font-mono)', color: 'var(--ink-secondary)' }}>
                  {row.rttMs === null ? '—' : `${formatNumber(row.rttMs, 1)} ms`}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}

function LogStatus({ level, label }: { level: PacketLogRow['result']['level']; label: string }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
      <StatusIcon level={level} size={12} />
      <span style={{ font: 'var(--text-body)' }}>{label}</span>
    </span>
  );
}
