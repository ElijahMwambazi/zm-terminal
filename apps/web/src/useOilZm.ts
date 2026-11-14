import { useCallback, useEffect, useState } from "react";
import { ApiError, getOilZmLatest, type OilZmMonth } from "./api";

export type OilZmState =
  | { status: "loading" }
  | { status: "ready"; data: OilZmMonth }
  | { status: "unavailable" } // no month has both stored Brent and stored BoZ FX
  | { status: "error"; message: string };

/** Reads the derived value from the API, which derives it from PostgreSQL only; `refresh` is an explicit re-read. */
export function useOilZm() {
  const [state, setState] = useState<OilZmState>({ status: "loading" });
  const load = useCallback(() => {
    setState({ status: "loading" });
    getOilZmLatest()
      .then((data) => setState(data ? { status: "ready", data } : { status: "unavailable" }))
      .catch((error: unknown) =>
        setState({ status: "error", message: error instanceof ApiError ? error.message : "ZM Terminal API is unavailable" }));
  }, []);
  useEffect(load, [load]);
  return { state, refresh: load };
}
