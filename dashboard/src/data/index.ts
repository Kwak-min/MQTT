import { createMockSource } from './mock/mockSource';
import { createHttpSource } from './http/httpSource';
import type { TelemetrySource } from './source';

/**
 * 데이터 소스는 환경 변수 하나로 갈아 끼웁니다.
 *   VITE_DATA_SOURCE=mock  (기본)
 *   VITE_DATA_SOURCE=http
 */
export function createTelemetrySource(): TelemetrySource {
  return import.meta.env.VITE_DATA_SOURCE === 'http' ? createHttpSource() : createMockSource();
}

export type { TelemetrySource, ControlCommand, ControlResult } from './source';
