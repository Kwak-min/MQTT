import { useState } from 'react';
import { useProtocol } from '@/data/hooks';
import { Card } from '@/components/common/Card';
import { HandshakeDiagram, HandshakeLegend } from '@/components/protocol/HandshakeDiagram';
import { ErrorState, LoadingState, EmptyState } from '@/components/common/States';
import { StatusIcon } from '@/components/common/StatusIcon';
import { formatTime } from '@/lib/format';
import type { QosLevel } from '@/types/domain';

export function ProtocolPage() {
  const [qos, setQos] = useState<QosLevel>(2);
  const { data, isPending, error, refetch } = useProtocol();

  if (error) return <main className="page"><ErrorState error={error} onRetry={refetch} /></main>;
  if (isPending || !data) return <main className="page"><LoadingState /></main>;

  const flow = data.flows.find((f) => f.qos === qos) ?? data.flows[0];

  return (
    <main className="page">
      <div className="grid grid--4">
        <Card title="활성 세션">
          <span style={{ font: 'var(--text-metric)' }}>
            {data.session.activeSessions}
            <span style={{ font: '500 15px/1 var(--font-sans)', color: 'var(--ink-secondary)' }}> / {data.session.totalSessions}</span>
          </span>
        </Card>
        <Card title="게이트웨이">
          <span style={{ font: '600 24px/1 var(--font-sans)' }}>{data.session.gatewayOnline ? 'ONLINE' : 'OFFLINE'}</span>
          <span className="card__meta">
            {data.session.gatewayTransport} · 포트 {data.session.gatewayPort}
          </span>
        </Card>
        <Card title="Keep-Alive">
          <span style={{ font: '600 24px/1 var(--font-sans)', color: data.session.keepAliveSeconds === null ? 'var(--ink-muted)' : undefined }}>
            {data.session.keepAliveSeconds === null ? '미구현' : `${data.session.keepAliveSeconds} s`}
          </span>
          <span className="card__meta">게이트웨이에 keep-alive 개념이 아직 없습니다</span>
        </Card>
        <Card title="현재 QoS 흐름">
          <span style={{ font: '600 24px/1 var(--font-sans)' }}>QoS {flow?.qos ?? '—'}</span>
          <span className="card__meta">Msg ID {flow?.msgId ?? '—'}</span>
        </Card>
      </div>

      <div className="grid grid--main">
        <Card
          title="Packet Handshake"
          titleEn="핸드셰이크 흐름"
          actions={
            <div className="segmented" role="group" aria-label="QoS 흐름 선택">
              {([0, 1, 2] as QosLevel[]).map((q) => (
                <button key={q} type="button" aria-pressed={qos === q} onClick={() => setQos(q)}>
                  QoS {q}
                </button>
              ))}
            </div>
          }
          footer={<HandshakeLegend />}
        >
          {flow ? <HandshakeDiagram flow={flow} /> : <EmptyState label="핸드셰이크 데이터가 없습니다" />}
        </Card>

        <div className="stack">
          <Card title="Packet Metadata" titleEn="패킷 메타데이터">
            {data.packetMetadata.length === 0 ? (
              <EmptyState label="메타데이터가 아직 없습니다" />
            ) : (
              <dl style={{ margin: 0, display: 'flex', flexDirection: 'column' }}>
                {data.packetMetadata.map((m, i) => (
                  <div
                    key={m.field}
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      padding: '8px 0',
                      borderBottom: i === data.packetMetadata.length - 1 ? 'none' : '1px solid var(--line-hairline)',
                    }}
                  >
                    <dt style={{ font: 'var(--text-body)', color: 'var(--ink-secondary)' }}>{m.field}</dt>
                    <dd className="mono" style={{ margin: 0, font: '500 12.5px/1.5 var(--font-mono)' }}>
                      {m.value}
                    </dd>
                  </div>
                ))}
              </dl>
            )}
          </Card>

          <Card title="Event Timeline" titleEn="이벤트 타임라인">
            {data.timeline.length === 0 ? (
              <EmptyState label="기록된 이벤트가 없습니다" />
            ) : (
              <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 10 }}>
                {data.timeline.map((e) => (
                  <li key={`${e.at}-${e.event}`} style={{ display: 'flex', gap: 9, alignItems: 'flex-start' }}>
                    <span style={{ marginTop: 2 }}>
                      <StatusIcon level={e.level} size={13} />
                    </span>
                    <span style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                      <span className="mono" style={{ font: 'var(--text-caption)', color: 'var(--ink-muted)' }}>
                        {formatTime(e.at)}
                      </span>
                      <span style={{ font: 'var(--text-body-strong)' }}>{e.event}</span>
                      <span style={{ font: 'var(--text-body)', color: 'var(--ink-secondary)' }}>{e.detail}</span>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>
    </main>
  );
}
