import { useCallback, useEffect, useState } from "react";
import { ApiError, getFuelLatest, type FuelLatest } from "./api";

export type FuelState =
  | { status: "loading" }
  | { status: "ready"; data: FuelLatest }
  | { status: "error"; message: string };

/** Reads stored ERB prices (the API never calls ERB); `refresh` is an explicit re-read. */
export function useFuel() {
  const [state, setState] = useState<FuelState>({ status: "loading" });
  const load = useCallback(() => {
    setState({ status: "loading" });
    getFuelLatest()
      .then((data) => setState({ status: "ready", data }))
      .catch((error: unknown) =>
        setState({ status: "error", message: error instanceof ApiError && error.message.includes("(503)")
          ? "Stored fuel prices are unavailable (database)" : "ZM Terminal API is unavailable" }));
  }, []);
  useEffect(load, [load]);
  return { state, refresh: load };
}
