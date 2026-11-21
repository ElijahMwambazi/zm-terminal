import { useCallback, useEffect, useState } from "react";
import { ApiError, getEnergyGenerationLatest, type EnergyGenerationLatest } from "./api";

export type EnergyState =
  | { status: "loading" }
  | { status: "ready"; data: EnergyGenerationLatest }
  | { status: "error"; message: string };

/** Reads the stored ERB generation snapshot (the API never calls ERB); `refresh` only re-reads PostgreSQL through the API. No polling. */
export function useEnergyGeneration() {
  const [state, setState] = useState<EnergyState>({ status: "loading" });
  const load = useCallback(() => {
    let cancelled = false;
    setState({ status: "loading" });
    getEnergyGenerationLatest()
      .then((data) => !cancelled && setState({ status: "ready", data }))
      .catch((error: unknown) =>
        !cancelled && setState({ status: "error", message: error instanceof ApiError ? error.message : "ZM Terminal API is unavailable" }));
    return () => { cancelled = true; };
  }, []);
  useEffect(load, [load]);
  return { state, refresh: load };
}
