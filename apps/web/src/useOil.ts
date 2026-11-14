import { useCallback, useEffect, useState } from "react";
import { ApiError, getOilLatest, type OilLatest } from "./api";

export type OilState =
  | { status: "loading" }
  | { status: "ready"; data: OilLatest }
  | { status: "error"; message: string };

/** One live OpenBB/FRED read per load or explicit refresh; never polled. */
export function useOil() {
  const [state, setState] = useState<OilState>({ status: "loading" });
  const load = useCallback(() => {
    setState({ status: "loading" });
    getOilLatest()
      .then((data) => setState({ status: "ready", data }))
      .catch((error: unknown) =>
        setState({ status: "error", message: error instanceof ApiError ? error.message : "ZM Terminal API is unavailable" }));
  }, []);
  useEffect(load, [load]);
  return { state, refresh: load };
}
