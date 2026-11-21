import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getEnergyGenerationLatest, type EnergyGenerationLatest } from "./api";

export type EnergyState =
  | { status: "loading" }
  | { status: "ready"; data: EnergyGenerationLatest; refreshing?: boolean }
  | { status: "error"; message: string };

/** Reads the stored ERB generation snapshot (the API never calls ERB); `refresh` only re-reads PostgreSQL through the API. No polling.
 * Newest request wins: every load takes a generation number and a response is applied only if it is still the newest
 * (and the component is still mounted). A refresh keeps the previous data visible instead of blanking it. */
export function useEnergyGeneration() {
  const [state, setState] = useState<EnergyState>({ status: "loading" });
  const generation = useRef(0);
  const load = useCallback(() => {
    const id = ++generation.current;
    setState((prev) => (prev.status === "ready" ? { ...prev, refreshing: true } : { status: "loading" }));
    getEnergyGenerationLatest()
      .then((data) => { if (id === generation.current) setState({ status: "ready", data }); })
      .catch((error: unknown) => {
        if (id === generation.current) setState({ status: "error", message: error instanceof ApiError ? error.message : "ZM Terminal API is unavailable" });
      });
  }, []);
  useEffect(() => {
    load();
    return () => { generation.current += 1; }; // unmount (or effect re-run) invalidates anything in flight
  }, [load]);
  return { state, refresh: load };
}
