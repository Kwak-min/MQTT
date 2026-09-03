import type { StatusLevel } from '@/types/domain';
import { statusVar } from '@/lib/format';

/**
 * 상태는 색만으로 전달하지 않습니다. 이 아이콘은 항상 라벨과 짝을 이루며,
 * 모양 자체가 상태를 구분하므로 색각 이상에서도 읽힙니다.
 */
export function StatusIcon({ level, size = 14 }: { level: StatusLevel; size?: number }) {
  const color = statusVar(level);
  const common = { width: size, height: size, viewBox: '0 0 16 16', fill: 'none', 'aria-hidden': true } as const;

  if (level === 'normal') {
    return (
      <svg {...common}>
        <circle cx="8" cy="8" r="6.5" stroke={color} strokeWidth="1.7" />
        <path d="M5.2 8.2 7 10l3.8-4" stroke={color} strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }
  if (level === 'warning') {
    return (
      <svg {...common}>
        <circle cx="8" cy="8" r="6.5" stroke={color} strokeWidth="1.7" />
        <path d="M8 4.8v4" stroke={color} strokeWidth="1.7" strokeLinecap="round" />
        <circle cx="8" cy="11.1" r="0.95" fill={color} />
      </svg>
    );
  }
  if (level === 'serious') {
    return (
      <svg {...common}>
        <path d="M8 2.6 14.4 13H1.6z" stroke={color} strokeWidth="1.7" strokeLinejoin="round" />
        <path d="M8 6.6v3" stroke={color} strokeWidth="1.7" strokeLinecap="round" />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <circle cx="8" cy="8" r="6.5" stroke={color} strokeWidth="1.7" />
      <path d="M5.8 5.8l4.4 4.4M10.2 5.8l-4.4 4.4" stroke={color} strokeWidth="1.7" strokeLinecap="round" />
    </svg>
  );
}

export function StatusPill({ level, label, detail }: { level: StatusLevel; label: string; detail?: string }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
      <StatusIcon level={level} size={15} />
      <span style={{ font: 'var(--text-body-strong)' }}>{label}</span>
      {detail ? <span style={{ font: 'var(--text-body)', color: 'var(--ink-secondary)' }}>{detail}</span> : null}
    </span>
  );
}
