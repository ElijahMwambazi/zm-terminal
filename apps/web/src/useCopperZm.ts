import { useCallback, useEffect, useState } from "react";
import { ApiError, getCopperZmLatest, type CopperZmMonth } from "./api";

export type CopperZmState =
  | { status: "loading" }
  | { status: "ready"; data: CopperZmMonth }
  | { status: "unavailable" } // no month has both stored IMF Copper and stored BoZ FX
  | { status: "error"; message: string };

/** Reads the derived value from the API, which derives it from PostgreSQL only (never the IMF or BoZ); `refresh` re-reads it. */
export function useCopperZm() {
  const [state, setState] = useState<CopperZmState>({ status: "loading" });
  const load = useCallback(() => {
    let cancelled = false;
    setState({ status: "loading" });
    getCopperZmLatest()
      .then((data) => !cancelled && setState(data ? { status: "ready", data } : { status: "unavailable" }))
      .catch((error: unknown) =>
        !cancelled && setState({ status: "error", message: error instanceof ApiError ? error.message : "ZM Terminal API is unavailable" }));
    return () => { cancelled = true; };
  }, []);
  useEffect(load, [load]);
  return { state, refresh: load };
}
