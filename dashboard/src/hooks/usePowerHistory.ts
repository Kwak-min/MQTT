import { useEffect, useState } from "react";
import { useTelemetrySource } from "./useTelemetrySource";
import type { PowerSample } from "@/lib/dataSource/types";

/**
 * 전력/에너지 로그를 주기적으로 다시 조회합니다. subscribeTelemetry 같은 실시간
 * 구독 채널이 없는 PowerSample 특성상, 짧은 폴링으로 "실시간처럼" 갱신합니다.
 */
export function usePowerHistory(nodeId: string | null, limitSamples = 60, pollMs = 3000) {
  const source = useTelemetrySource();
  const [samples, setSamples] = useState<PowerSample[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!nodeId) {
      setSamples([]);
      setLoading(false);
      return;
    }
    let cancelled = false;

    function load() {
      source.getPowerHistory(nodeId as string, limitSamples).then((data) => {
        if (cancelled) return;
        setSamples(data);
        setLoading(false);
      });
    }

    load();
    const id = setInterval(load, pollMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeId, source, limitSamples, pollMs]);

  return { samples, latest: samples[samples.length - 1] ?? null, loading };
}
