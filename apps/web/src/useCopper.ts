import { useCallback, useEffect, useState } from "react";
import { ApiError, getCopperLatest, type CopperLatest } from "./api";

export type CopperState =
  | { status: "loading" }
  | { status: "ready"; data: CopperLatest }
  | { status: "error"; message: string };

/** Reads the stored IMF Copper month (the API never calls the IMF); `refresh` only re-reads PostgreSQL through the API. */
export function useCopper() {
  const [state, setState] = useState<CopperState>({ status: "loading" });
  const load = useCallback(() => {
    let cancelled = false;
    setState({ status: "loading" });
    getCopperLatest()
      .then((data) => !cancelled && setState({ status: "ready", data }))
      .catch((error: unknown) =>
        !cancelled && setState({ status: "error", message: error instanceof ApiError ? error.message : "ZM Terminal API is unavailable" }));
    return () => { cancelled = true; };
  }, []);
  useEffect(load, [load]);
  return { state, refresh: load };
}
