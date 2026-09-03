import { StatusIcon } from './StatusIcon';

/** 실측 데이터가 붙기 전/붙은 뒤 모두 자주 보게 될 화면들입니다. */

export function LoadingState({ label = '데이터를 불러오는 중' }: { label?: string }) {
  return (
    <div className="state" role="status" aria-live="polite">
      <div className="skeleton" style={{ width: '60%', height: 14 }} />
      <div className="skeleton" style={{ width: '40%', height: 14 }} />
      <span>{label}…</span>
    </div>
  );
}

export function EmptyState({ label = '아직 수신된 데이터가 없습니다' }: { label?: string }) {
  return (
    <div className="state">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden>
        <rect x="3.5" y="5.5" width="17" height="13" rx="2.5" stroke="var(--ink-faint)" strokeWidth="1.6" />
        <path d="M3.5 10.5h17" stroke="var(--ink-faint)" strokeWidth="1.6" />
      </svg>
      <span>{label}</span>
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : '알 수 없는 오류';
  return (
    <div className="state" role="alert">
      <StatusIcon level="critical" size={20} />
      <span style={{ color: 'var(--ink-secondary)' }}>{message}</span>
      {onRetry ? (
        <button type="button" className="btn" onClick={onRetry}>
          다시 시도
        </button>
      ) : null}
    </div>
  );
}
