import { createHttpSource } from "./httpSource";
import { createMockSource } from "./mockSource";
import type { TelemetrySource } from "./types";

export * from "./types";

function readMode(): "mock" | "http" {
  const raw = (import.meta.env.VITE_DATA_SOURCE ?? "mock").toLowerCase();
  return raw === "http" ? "http" : "mock";
}

let cached: TelemetrySource | null = null;

/** 앱 전체에서 공유되는 단일 데이터 소스 인스턴스. */
export function getTelemetrySource(): TelemetrySource {
  if (cached) return cached;

  const mode = readMode();

  if (mode === "http") {
    const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
    const wsUrl = import.meta.env.VITE_WS_URL ?? "ws://localhost:8000/ws";
    cached = createHttpSource({ baseUrl, wsUrl });
  } else {
    const intervalMs = Number(import.meta.env.VITE_MOCK_INTERVAL_MS ?? 2000);
    cached = createMockSource(Number.isFinite(intervalMs) ? intervalMs : 2000);
  }

  return cached;
}

export function getDataSourceMode(): "mock" | "http" {
  return readMode();
}
