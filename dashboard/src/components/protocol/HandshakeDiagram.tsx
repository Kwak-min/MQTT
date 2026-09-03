import type { HandshakeFlow } from '@/types/domain';
import { formatNumber } from '@/lib/format';

const VIEW_W = 560;
const AXIS_X = 72;
const CLIENT_X = 126;
const GATEWAY_X = 410;
const TOP_Y = 28;
const FIRST_Y = 62;
const SPAN = 190;

/**
 * 시퀀스 다이어그램.
 *  - 화살표는 끊지 않고, 라벨은 화살표 위로 올립니다.
 *  - 왼쪽 시간축은 tMs에 실제로 비례합니다(균등 간격 아님).
 *  - 오른쪽 RTT 브래킷이 전체 왕복 시간을 묶습니다.
 *  - 상행(노드 송신)과 하행(게이트웨이 응답)을 색으로 구분합니다.
 *  - 재전송·패킷 유실은 별도 트랜잭션으로 분리해 아래에 그립니다.
 */
export function HandshakeDiagram({ flow }: { flow: HandshakeFlow }) {
  const maxT = Math.max(...flow.messages.map((m) => m.tMs), 0);
  const y = (t: number) => (maxT === 0 ? FIRST_Y : FIRST_Y + (t / maxT) * SPAN);

  const lastY = y(maxT);
  const retryTop = lastY + 38;
  const height = flow.retryCase ? retryTop + 80 : lastY + 46;
  const lifelineBottom = height - 12;

  return (
    <svg
      width="100%"
      viewBox={`0 0 ${VIEW_W} ${height}`}
      fill="none"
      role="img"
      aria-label={`QoS ${flow.qos} 핸드셰이크. 전체 RTT ${flow.rttMs} 밀리초.`}
    >
      <defs>
        <marker id="hsUp" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
          <path d="M0 0 L10 5 L0 10 z" fill="var(--series-gingerbread)" />
        </marker>
        <marker id="hsDown" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
          <path d="M0 0 L10 5 L0 10 z" fill="var(--ink-secondary)" />
        </marker>
        <marker id="hsLost" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
          <path d="M0 0 L10 5 L0 10 z" fill="var(--status-critical)" />
        </marker>
      </defs>

      <text x={CLIENT_X} y={16} fontSize="11.5" fontWeight="600" fill="var(--ink-primary)">
        Client · ESP32-S3
      </text>
      <text x={GATEWAY_X} y={16} textAnchor="end" fontSize="11.5" fontWeight="600" fill="var(--ink-primary)">
        Gateway · RPi 5
      </text>
      <text x={AXIS_X - 12} y={16} textAnchor="end" fontSize="10.5" fill="var(--ink-muted)">
        경과
      </text>

      <line x1={AXIS_X} y1={TOP_Y} x2={AXIS_X} y2={lifelineBottom} stroke="var(--line-grid)" strokeWidth="1" />
      <line x1={CLIENT_X} y1={TOP_Y} x2={CLIENT_X} y2={lifelineBottom} stroke="var(--line-axis)" strokeWidth="1" strokeDasharray="3 4" />
      <line x1={GATEWAY_X} y1={TOP_Y} x2={GATEWAY_X} y2={lifelineBottom} stroke="var(--line-axis)" strokeWidth="1" strokeDasharray="3 4" />

      {flow.messages.map((m) => {
        const my = y(m.tMs);
        const up = m.direction === 'up';
        return (
          <g key={`${m.name}-${m.tMs}`}>
            <text x={AXIS_X - 12} y={my + 4} textAnchor="end" fontFamily="var(--font-mono)" fontSize="11" fill="var(--ink-secondary)">
              {formatNumber(m.tMs, 1)}
            </text>
            <line x1={AXIS_X - 4} y1={my} x2={AXIS_X + 4} y2={my} stroke="var(--line-axis)" strokeWidth="1.5" />
            <text x={(CLIENT_X + GATEWAY_X) / 2} y={my - 8} textAnchor="middle" fontSize="12" fontWeight="600" fill="var(--ink-primary)">
              {m.name}
            </text>
            <line
              x1={up ? CLIENT_X : GATEWAY_X}
              y1={my}
              x2={up ? GATEWAY_X - 6 : CLIENT_X + 6}
              y2={my}
              stroke={up ? 'var(--series-gingerbread)' : 'var(--ink-secondary)'}
              strokeWidth="2"
              markerEnd={up ? 'url(#hsUp)' : 'url(#hsDown)'}
            />
          </g>
        );
      })}

      {maxT > 0 ? (
        <g>
          <path
            d={`M446 ${FIRST_Y} h10 v${lastY - FIRST_Y} h-10`}
            stroke="var(--line-axis)"
            strokeWidth="1.4"
            fill="none"
          />
          <text x={466} y={(FIRST_Y + lastY) / 2 - 6} fontSize="11" fill="var(--ink-muted)">
            RTT
          </text>
          <text x={466} y={(FIRST_Y + lastY) / 2 + 10} fontFamily="var(--font-mono)" fontSize="13" fontWeight="600" fill="var(--ink-primary)">
            {formatNumber(flow.rttMs, 1)} ms
          </text>
        </g>
      ) : (
        <text x={446} y={FIRST_Y + 4} fontSize="11" fill="var(--ink-muted)">
          응답 없음 · 단방향
        </text>
      )}

      {flow.retryCase && (
        <g>
          <line x1={AXIS_X - 20} y1={retryTop} x2={530} y2={retryTop} stroke="var(--line-grid)" strokeWidth="1" strokeDasharray="3 3" />
          <text x={AXIS_X - 20} y={retryTop + 19} fontSize="10.5" fill="var(--ink-muted)">
            별도 트랜잭션 · Msg ID {flow.retryCase.msgId}
          </text>
          <text x={240} y={retryTop + 41} textAnchor="middle" fontSize="11.5" fontWeight="500" fill="var(--status-critical-ink)">
            {flow.retryCase.label}
          </text>
          <line
            x1={CLIENT_X}
            y1={retryTop + 49}
            x2={330}
            y2={retryTop + 49}
            stroke="var(--status-critical)"
            strokeWidth="2"
            strokeDasharray="6 4"
            markerEnd="url(#hsLost)"
          />
          <path
            d={`M340 ${retryTop + 43} l12 12 M352 ${retryTop + 43} l-12 12`}
            stroke="var(--status-critical)"
            strokeWidth="2"
            strokeLinecap="round"
          />
          <text x={366} y={retryTop + 53} fontSize="11" fill="var(--ink-muted)">
            패킷 유실
          </text>
        </g>
      )}
    </svg>
  );
}

export function HandshakeLegend() {
  const items = [
    { label: '상행 · 노드 송신', color: 'var(--series-gingerbread)' },
    { label: '하행 · 게이트웨이 응답', color: 'var(--ink-secondary)' },
    { label: '재전송', color: 'var(--status-critical)' },
  ];
  return (
    <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap' }}>
      {items.map((it) => (
        <span key={it.label} style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
          <span style={{ width: 16, height: 2, background: it.color }} aria-hidden />
          <span style={{ font: 'var(--text-caption)', color: 'var(--ink-secondary)' }}>{it.label}</span>
        </span>
      ))}
    </div>
  );
}
