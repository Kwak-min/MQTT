import { useEffect, useState } from "react";
import { getTelemetrySource } from "@/lib/dataSource";
import type { ConnectionState } from "@/lib/dataSource/types";

export function useTelemetrySource() {
  return getTelemetrySource();
}

export function useConnectionState(): ConnectionState {
  const source = useTelemetrySource();
  const [state, setState] = useState<ConnectionState>(source.getConnectionState());

  useEffect(() => {
    setState(source.getConnectionState());
    return source.onConnectionStateChange(setState);
  }, [source]);

  return state;
}
