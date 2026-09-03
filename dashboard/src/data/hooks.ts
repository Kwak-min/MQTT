import { useCallback } from 'react';
import { createTelemetrySource } from '@/data';
import { useResource, type Resource } from '@/data/useResource';
import type { ControlCommand } from '@/data/source';
import type { ExperimentQuery, ExperimentSummary, ProtocolSnapshot } from '@/types/domain';

/** 모듈 스코프 싱글턴. 테스트에서는 source를 주입하도록 바꾸면 됩니다. */
export const source = createTelemetrySource();
export const dataSourceKind = source.kind;

/** 운영 스냅샷은 여러 화면이 공유하므로 OperationsContext에서 한 번만 만듭니다. */
export function useProtocol(): Resource<ProtocolSnapshot> {
  return useResource((signal) => source.getProtocolSnapshot(signal), []);
}

export function useExperiment(query: ExperimentQuery): Resource<ExperimentSummary> {
  return useResource((signal) => source.getExperimentSummary(query, signal), [query.qos, query.rangeMinutes]);
}

export function useControl() {
  return useCallback((command: ControlCommand) => source.sendControl(command), []);
}
