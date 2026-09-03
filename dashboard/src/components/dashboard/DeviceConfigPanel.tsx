import type { ConfigEntry } from '@/types/domain';
import { Card } from '@/components/common/Card';
import { EmptyState } from '@/components/common/States';

/** 라벨만 있던 알약 칩을 라벨-값 쌍으로 바꿨습니다. 편집 진입점도 함께 둡니다. */
export function DeviceConfigPanel({ entries, onEdit }: { entries: ConfigEntry[]; onEdit?: () => void }) {
  return (
    <Card
      title="Device Config"
      titleEn="설정"
      actions={
        <button type="button" className="btn" onClick={onEdit} disabled={!onEdit}>
          편집
        </button>
      }
    >
      {entries.length === 0 ? (
        <EmptyState label="설정을 불러오지 못했습니다" />
      ) : (
        <dl style={{ margin: 0, display: 'flex', flexDirection: 'column' }}>
          {entries.map((entry, i) => (
            <div
              key={entry.key}
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                padding: '8px 0',
                borderBottom: i === entries.length - 1 ? 'none' : '1px solid var(--line-hairline)',
              }}
            >
              <dt style={{ font: 'var(--text-body)', color: 'var(--ink-secondary)' }}>{entry.label}</dt>
              <dd className="mono" style={{ margin: 0, font: '500 12.5px/1.5 var(--font-mono)' }}>
                {entry.value}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </Card>
  );
}
