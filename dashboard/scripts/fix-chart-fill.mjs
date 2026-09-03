#!/usr/bin/env node
/**
 * fix-chart-fill.mjs
 * ---------------------------------------------------------------------------
 * 문제: 옆 카드(Network Quality, 게이지 4줄)에 맞춰 카드 높이는 늘어나는데
 *       차트 높이는 고정이라 "누적 소비 전력" 카드 아래가 크게 빕니다.
 *
 * 해결: 차트 컨테이너가 카드의 남는 높이를 흡수하고, 차트는 컨테이너의
 *       실측 크기를 viewBox로 그대로 써서 확대·축소 없이 1:1로 그립니다.
 *       (실측 크기를 쓰므로 글자 크기와 선 두께가 왜곡되지 않습니다.)
 *
 * 바뀌는 파일
 *   + src/lib/useElementSize.ts                     (신규)
 *   ~ src/styles/global.css                         (.chart-fill 추가)
 *   ~ src/components/common/LineChart.tsx           (width prop 주입)
 *   ~ src/components/dashboard/PowerTrendPanel.tsx  (채움형 적용)
 *   ~ src/pages/ExperimentPage.tsx                  (지연 추이도 동일 적용)
 *
 * 사용법 — 프로젝트 루트에서:
 *   node scripts/fix-chart-fill.mjs           적용
 *   node scripts/fix-chart-fill.mjs --check   무엇이 바뀔지만 출력
 *
 * 여러 번 실행해도 안전합니다(이미 적용됐으면 건너뜁니다).
 * 원본은 <파일>.bak 으로 남습니다.
 */

import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';

const DRY = process.argv.includes('--check');
const ROOT = process.cwd();

const log = { changed: [], skipped: [], failed: [] };

function read(rel) {
  const p = resolve(ROOT, rel);
  if (!existsSync(p)) throw new Error(`파일이 없습니다: ${rel} (프로젝트 루트에서 실행했는지 확인하세요)`);
  return readFileSync(p, 'utf8');
}

function write(rel, next, original) {
  if (DRY) return;
  const p = resolve(ROOT, rel);
  mkdirSync(dirname(p), { recursive: true });
  if (original !== undefined && !existsSync(`${p}.bak`)) writeFileSync(`${p}.bak`, original, 'utf8');
  writeFileSync(p, next, 'utf8');
}

/** 앵커를 정확히 1회 치환. 못 찾으면 실패로 기록하고 파일은 건드리지 않습니다. */
function patch(rel, edits, alreadyApplied) {
  let src;
  try {
    src = read(rel);
  } catch (err) {
    log.failed.push([rel, err.message]);
    return;
  }

  if (alreadyApplied(src)) {
    log.skipped.push([rel, '이미 적용됨']);
    return;
  }

  let next = src;
  for (const [find, replace, opts = {}] of edits) {
    const count = next.split(find).length - 1;
    const expected = opts.all ? count : 1;
    if (count === 0 || (!opts.all && count !== 1)) {
      log.failed.push([rel, `앵커를 ${count}번 찾았습니다(기대: ${expected}). 파일이 이미 수정된 것 같습니다.\n      찾는 문자열: ${find.split('\n')[0].trim().slice(0, 70)}…`]);
      return;
    }
    next = opts.all ? next.split(find).join(replace) : next.replace(find, replace);
  }

  write(rel, next, src);
  log.changed.push([rel, `${edits.length}곳 수정`]);
}

/* ------------------------------------------------------------------ */
/* 1. 크기 관찰 훅 (신규 파일)                                          */
/* ------------------------------------------------------------------ */

const USE_ELEMENT_SIZE = `import { useCallback, useEffect, useRef, useState } from 'react';

export interface Size {
  width: number;
  height: number;
}

/**
 * 요소의 실제 픽셀 크기를 관찰합니다.
 *
 * 차트를 카드의 남는 공간에 정확히 맞출 때 씁니다. 측정한 크기를 그대로
 * viewBox로 쓰면 확대·축소가 일어나지 않아 글자 크기와 선 두께가 정확합니다.
 *
 * ref를 붙인 요소는 \`.chart-fill\`(position: relative; flex: 1 1 0)이어야 하고
 * 내부 SVG는 absolute라야 합니다. 자식이 부모 높이에 영향을 주지 않아야
 * 리사이즈 루프가 생기지 않습니다.
 */
export function useElementSize(): [(node: HTMLElement | null) => void, Size] {
  const [size, setSize] = useState<Size>({ width: 0, height: 0 });
  const observerRef = useRef<ResizeObserver | null>(null);

  const ref = useCallback((node: HTMLElement | null) => {
    observerRef.current?.disconnect();
    observerRef.current = null;
    if (!node) return;

    const rect = node.getBoundingClientRect();
    setSize({ width: rect.width, height: rect.height });

    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect;
      if (!box) return;
      // 소수점 흔들림으로 인한 불필요한 리렌더를 막습니다.
      setSize((prev) =>
        Math.abs(prev.width - box.width) > 0.5 || Math.abs(prev.height - box.height) > 0.5
          ? { width: box.width, height: box.height }
          : prev,
      );
    });
    observer.observe(node);
    observerRef.current = observer;
  }, []);

  useEffect(() => () => observerRef.current?.disconnect(), []);

  return [ref, size];
}
`;

const hookPath = 'src/lib/useElementSize.ts';
if (existsSync(resolve(ROOT, hookPath))) {
  log.skipped.push([hookPath, '이미 있음']);
} else {
  write(hookPath, USE_ELEMENT_SIZE);
  log.changed.push([hookPath, '생성']);
}

/* ------------------------------------------------------------------ */
/* 2. global.css — 채움형 차트 컨테이너                                 */
/* ------------------------------------------------------------------ */

patch(
  'src/styles/global.css',
  [
    [
      '.card__foot {',
      `/* 카드가 옆 카드에 맞춰 늘어날 때, 차트가 남는 높이를 채웁니다.
   자식 SVG는 absolute라 부모 높이에 영향을 주지 않습니다(리사이즈 루프 방지). */
.chart-fill {
  position: relative;
  flex: 1 1 0;
  min-height: 180px;
}
.chart-fill > svg {
  position: absolute;
  inset: 0;
}

.card__foot {`,
    ],
  ],
  (s) => s.includes('.chart-fill'),
);

/* ------------------------------------------------------------------ */
/* 3. LineChart — 좌표계 너비를 주입 가능하게                            */
/* ------------------------------------------------------------------ */

patch(
  'src/components/common/LineChart.tsx',
  [
    [
      `  height?: number;
  /** 임계값 가로 점선 */`,
      `  height?: number;
  /** 좌표계 너비. 컨테이너 실측값을 넘기면 확대·축소 없이 1:1로 그립니다. */
  width?: number;
  /** 임계값 가로 점선 */`,
    ],
    ['const VIEW_W = 860;', 'const DEFAULT_VIEW_W = 860;'],
    [
      `export function LineChart({ series, xTicks, yTicks, xDomain, yDomain, height = 150, thresholdY, ariaLabel }: Props) {
  const id = useId();
  const innerW = VIEW_W - PAD.left - PAD.right;`,
      `export function LineChart({ series, xTicks, yTicks, xDomain, yDomain, height = 150, width, thresholdY, ariaLabel }: Props) {
  const id = useId();
  const viewW = width && width > 200 ? width : DEFAULT_VIEW_W;
  const innerW = viewW - PAD.left - PAD.right;`,
    ],
    [
      '<svg width="100%" viewBox={`0 0 ${VIEW_W} ${height}`} fill="none" role="img" aria-label={ariaLabel}>',
      '<svg width="100%" height="100%" viewBox={`0 0 ${viewW} ${height}`} fill="none" role="img" aria-label={ariaLabel}>',
    ],
    ['VIEW_W - PAD.right', 'viewW - PAD.right', { all: true }],
  ],
  (s) => s.includes('DEFAULT_VIEW_W'),
);

/* ------------------------------------------------------------------ */
/* 4. PowerTrendPanel — 문제의 카드                                     */
/* ------------------------------------------------------------------ */

patch(
  'src/components/dashboard/PowerTrendPanel.tsx',
  [
    [
      "import { formatNumber } from '@/lib/format';",
      "import { formatNumber } from '@/lib/format';\nimport { useElementSize } from '@/lib/useElementSize';",
    ],
    [
      `export function PowerTrendPanel({ trend }: { trend: PowerTrend }) {
  if (trend.points.length < 2) {`,
      `export function PowerTrendPanel({ trend }: { trend: PowerTrend }) {
  const [chartRef, chartSize] = useElementSize();

  if (trend.points.length < 2) {`,
    ],
    [
      `      <LineChart
        ariaLabel="누적 소비 전력 비교"
        height={170}`,
      `      <div className="chart-fill" ref={chartRef}>
      <LineChart
        ariaLabel="누적 소비 전력 비교"
        height={Math.max(180, chartSize.height)}
        width={chartSize.width}`,
    ],
    [
      `        ]}
      />
    </Card>`,
      `        ]}
      />
      </div>
    </Card>`,
    ],
  ],
  (s) => s.includes('chart-fill'),
);

/* ------------------------------------------------------------------ */
/* 5. ExperimentPage — 지연 추이 차트도 같은 문제                        */
/* ------------------------------------------------------------------ */

patch(
  'src/pages/ExperimentPage.tsx',
  [
    [
      "import { baselineDelta, formatNumber } from '@/lib/format';",
      "import { baselineDelta, formatNumber } from '@/lib/format';\nimport { useElementSize } from '@/lib/useElementSize';",
    ],
    [
      `function LatencyChart({ points }: { points: { minute: number; gingerbread: number; legacy: number }[] }) {
  const maxMinute = points[points.length - 1].minute;`,
      `function LatencyChart({ points }: { points: { minute: number; gingerbread: number; legacy: number }[] }) {
  const [chartRef, chartSize] = useElementSize();
  const maxMinute = points[points.length - 1].minute;`,
    ],
    [
      `  return (
    <LineChart
      ariaLabel="지연 시간 추이 비교"
      height={200}`,
      `  return (
    <div className="chart-fill" ref={chartRef}>
    <LineChart
      ariaLabel="지연 시간 추이 비교"
      height={Math.max(200, chartSize.height)}
      width={chartSize.width}`,
    ],
    [
      `        { x: maxMinute, label: \`\${maxMinute} min\` },
      ]}
    />
  );
}`,
      `        { x: maxMinute, label: \`\${maxMinute} min\` },
      ]}
    />
    </div>
  );
}`,
    ],
  ],
  (s) => s.includes('chart-fill'),
);

/* ------------------------------------------------------------------ */
/* 결과 보고                                                            */
/* ------------------------------------------------------------------ */

const line = '─'.repeat(64);
console.log(`\n${DRY ? '[검사 모드 — 파일을 바꾸지 않습니다]' : '[적용]'} 차트 채움 높이 수정`);
console.log(line);
for (const [file, note] of log.changed) console.log(`  수정   ${file}  — ${note}`);
for (const [file, note] of log.skipped) console.log(`  건너뜀 ${file}  — ${note}`);
for (const [file, note] of log.failed) console.log(`  실패   ${file}\n      ${note}`);
console.log(line);

if (log.failed.length) {
  console.log(`
실패한 파일은 손대지 않았습니다. 해당 파일을 이미 수정하셨다면
아래 내용을 손으로 반영하시면 됩니다.

  1) src/styles/global.css 에 .chart-fill 규칙 추가
  2) LineChart 에 width prop 을 받아 viewBox 너비로 사용
  3) 차트를 <div className="chart-fill" ref={chartRef}> 로 감싸고
     useElementSize() 의 실측값을 height / width 로 전달
`);
  process.exit(1);
}

if (!DRY && log.changed.length) {
  console.log(`
다음 단계:
  npm run typecheck    타입 확인
  npm run dev          브라우저에서 확인

되돌리려면 함께 만들어진 <파일>.bak 을 복원하세요.
`);
}
