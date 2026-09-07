import { useEffect, useState } from "react";
import { useTelemetrySource } from "./useTelemetrySource";
import type { NodeInfo } from "@/lib/dataSource/types";

export function useNodes() {
  const source = useTelemetrySource();
  const [nodes, setNodes] = useState<NodeInfo[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    source.listNodes().then((list) => {
      if (!cancelled) {
        setNodes(list);
        setLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [source]);

  return { nodes, loading };
}
