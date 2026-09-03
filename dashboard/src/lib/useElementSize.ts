import { useCallback, useEffect, useRef, useState } from 'react';

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
 * ref를 붙인 요소는 `.chart-fill`(position: relative; flex: 1 1 0)이어야 하고
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
