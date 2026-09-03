import { createContext, useContext, useEffect, useMemo, type ReactNode } from 'react';
import { source } from '@/data/hooks';
import { useResource, type Resource } from '@/data/useResource';
import type { OperationsSnapshot } from '@/types/domain';

const OperationsContext = createContext<Resource<OperationsSnapshot> | null>(null);

/**
 * 운영 스냅샷 단일 소스.
 * 스트림(subscribe)이 있으면 스트림이 갱신하고, 없으면 5초 폴링으로 대체합니다.
 * 백엔드에 WebSocket이 붙으면 httpSource에 subscribe만 구현하면 되고
 * 화면 코드는 그대로입니다.
 */
export function OperationsProvider({ children }: { children: ReactNode }) {
  const streaming = typeof source.subscribe === 'function';
  const resource = useResource(
    (signal) => source.getOperationsSnapshot(signal),
    [],
    { pollMs: streaming ? 0 : 5000 },
  );

  const { setData } = resource;
  useEffect(() => {
    if (!source.subscribe) return;
    return source.subscribe((snapshot) => setData(snapshot));
  }, [setData]);

  const value = useMemo(() => resource, [resource.data, resource.error, resource.isFetching, resource.refetch, resource.setData]);

  return <OperationsContext.Provider value={value}>{children}</OperationsContext.Provider>;
}

export function useOperations(): Resource<OperationsSnapshot> {
  const ctx = useContext(OperationsContext);
  if (!ctx) throw new Error('useOperations는 OperationsProvider 안에서만 쓸 수 있습니다.');
  return ctx;
}
