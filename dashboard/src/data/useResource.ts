import { useCallback, useEffect, useRef, useState } from 'react';

export interface Resource<T> {
  data: T | undefined;
  isPending: boolean;
  isFetching: boolean;
  error: unknown;
  refetch: () => void;
  /** 스트림·낙관적 갱신 등 외부에서 값을 밀어 넣을 때 사용합니다. */
  setData: (next: T) => void;
}

interface Options {
  /** 폴링 주기(ms). 0이나 undefined면 폴링하지 않습니다. */
  pollMs?: number;
  enabled?: boolean;
}

/**
 * 요청 1건을 관리하는 최소 훅. AbortController, 폴링, 재시도, 언마운트 정리를 담당합니다.
 *
 * 나중에 캐시·중복 제거·낙관적 업데이트가 필요해지면 이 훅의 구현만
 * TanStack Query 등으로 교체하면 됩니다. 호출부 시그니처는 그대로 둘 수 있습니다.
 */
export function useResource<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[],
  options: Options = {},
): Resource<T> {
  const { pollMs = 0, enabled = true } = options;

  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<unknown>(null);
  const [isFetching, setFetching] = useState(false);
  const [token, setToken] = useState(0);

  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const refetch = useCallback(() => setToken((n) => n + 1), []);

  useEffect(() => {
    if (!enabled) return;

    const controller = new AbortController();
    let cancelled = false;
    let timer: number | undefined;

    const run = async () => {
      setFetching(true);
      try {
        const next = await fetcherRef.current(controller.signal);
        if (cancelled) return;
        setData(next);
        setError(null);
      } catch (err) {
        if (cancelled || controller.signal.aborted) return;
        setError(err);
      } finally {
        if (!cancelled) setFetching(false);
      }
    };

    void run();
    if (pollMs > 0) timer = window.setInterval(run, pollMs);

    return () => {
      cancelled = true;
      controller.abort();
      if (timer !== undefined) window.clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, token, pollMs, enabled]);

  return {
    data,
    isPending: data === undefined && error === null,
    isFetching,
    error,
    refetch,
    setData,
  };
}
