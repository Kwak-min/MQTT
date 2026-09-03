import { NavLink } from 'react-router-dom';
import { dataSourceKind } from '@/data/hooks';

const TABS = [
  { to: '/', label: '운영', labelEn: 'Operations', end: true },
  { to: '/experiment', label: '실험', labelEn: 'Experiment', end: false },
  { to: '/protocol', label: '프로토콜', labelEn: 'Protocol', end: false },
];

/** 시안에서 빠져 있던 전역 헤더. 3화면을 잇고 연결 상태를 한 곳에 모읍니다. */
export function AppHeader({ connected, updatedLabel }: { connected: boolean; updatedLabel: string }) {
  return (
    <header
      style={{
        background: 'var(--surface-card)',
        borderBottom: '1px solid var(--line-card)',
        padding: '0 40px',
        display: 'flex',
        alignItems: 'center',
        gap: 32,
        height: 60,
      }}
    >
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
        <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden>
          <rect x="2.5" y="2.5" width="15" height="15" rx="3.5" stroke="var(--ink-primary)" strokeWidth="1.6" />
          <path d="M6.5 12.5 9 9l2.2 2.2L13.5 7" stroke="var(--series-gingerbread)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <strong style={{ font: '600 15px/1 var(--font-sans)', letterSpacing: '-0.01em' }}>Gingerbread</strong>
      </span>

      <nav style={{ display: 'flex', gap: 4 }}>
        {TABS.map((tab) => (
          <NavLink
            key={tab.to}
            to={tab.to}
            end={tab.end}
            style={({ isActive }) => ({
              font: `${isActive ? 600 : 500} 13.5px/1 var(--font-sans)`,
              color: isActive ? 'var(--ink-primary)' : 'var(--ink-secondary)',
              background: isActive ? 'var(--surface-selected)' : 'transparent',
              padding: '8px 14px',
              borderRadius: 'var(--radius-control)',
              textDecoration: 'none',
            })}
          >
            {tab.label} <span style={{ color: 'var(--ink-faint)', fontWeight: 400 }}>{tab.labelEn}</span>
          </NavLink>
        ))}
      </nav>

      <span className="spacer" />

      {dataSourceKind === 'mock' && (
        <span
          style={{
            font: 'var(--text-caption)',
            color: 'var(--ink-secondary)',
            border: '1px solid var(--line-control)',
            borderRadius: 'var(--radius-chip)',
            padding: '3px 8px',
          }}
          title="VITE_DATA_SOURCE=http 로 바꾸면 실제 게이트웨이에 붙습니다"
        >
          MOCK DATA
        </span>
      )}

      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
        <span
          style={{
            width: 7,
            height: 7,
            borderRadius: '50%',
            background: connected ? 'var(--status-normal)' : 'var(--status-critical)',
          }}
          aria-hidden
        />
        <span style={{ font: 'var(--text-body)', color: 'var(--ink-secondary)' }}>
          {connected ? '게이트웨이 연결됨' : '게이트웨이 끊김'}
        </span>
        <span style={{ font: 'var(--text-body)', color: 'var(--ink-muted)' }}>· {updatedLabel}</span>
      </span>
    </header>
  );
}
