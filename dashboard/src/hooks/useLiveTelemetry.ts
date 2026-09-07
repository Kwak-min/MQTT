import { useEffect, useRef, useState } from "react";
import { useTelemetrySource } from "./useTelemetrySource";
import type { TelemetrySample } from "@/lib/dataSource/types";

export function useLiveTelemetry(nodeId: string | null, historyLength = 60) {
  const source = useTelemetrySource();
  const [samples, setSamples] = useState<TelemetrySample[]>([]);
  const [loading, setLoading] = useState(true);
  const maxLen = useRef(historyLength);
  maxLen.current = historyLength;

  useEffect(() => {
    if (!nodeId) return;
    let cancelled = false;
    setLoading(true);

    source.getTelemetryHistory(nodeId, historyLength).then((history) => {
      if (cancelled) return;
      setSamples(history);
      setLoading(false);
    });

    const unsubscribe = source.subscribeTelemetry(nodeId, (sample) => {
      setSamples((prev) => {
        const next = [...prev, sample];
        if (next.length > maxLen.current) next.shift();
        return next;
      });
    });

    return () => {
      cancelled = true;
      unsubscribe();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeId, source]);

  return { samples, latest: samples[samples.length - 1] ?? null, loading };
}
