import type {
  ExperimentQuery,
  ExperimentSummary,
  OperationsSnapshot,
  ProtocolSnapshot,
  QosLevel,
} from '@/types/domain';

/**
 * 화면이 데이터를 얻는 유일한 통로.
 * mock 구현과 http 구현이 이 인터페이스를 공유하므로, 백엔드가 붙어도
 * 컴포넌트는 한 줄도 바뀌지 않습니다.
 */
export interface TelemetrySource {
  readonly kind: 'mock' | 'http';
  getOperationsSnapshot(signal?: AbortSignal): Promise<OperationsSnapshot>;
  getExperimentSummary(query: ExperimentQuery, signal?: AbortSignal): Promise<ExperimentSummary>;
  getProtocolSnapshot(signal?: AbortSignal): Promise<ProtocolSnapshot>;
  /** 다운링크 제어. mock은 로그만 남기고 성공을 반환합니다. */
  sendControl(input: ControlCommand): Promise<ControlResult>;
  /**
   * 실시간 스트림. 지원하지 않으면 undefined.
   * 구독하면 정리 함수를 돌려줍니다.
   */
  subscribe?(onSnapshot: (snapshot: OperationsSnapshot) => void): () => void;
}

export interface ControlCommand {
  deviceIp: string;
  devicePort: number;
  qosLevel: QosLevel;
  sleepIntervalMs: number;
}

export interface ControlResult {
  ok: boolean;
  message: string;
}
