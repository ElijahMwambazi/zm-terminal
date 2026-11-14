import { useCallback, useEffect, useState } from "react";
import { ApiError, getYieldCurve, type YieldCurve } from "./api";

export type YieldCurveState =
  | { status: "loading" }
  | { status: "ready"; data: YieldCurve }
  | { status: "unavailable" } // no stored accepted observation on or before the date
  | { status: "error"; message: string };

/** Reads the derived snapshot from the API (stored data only). asOf null means latest; `refresh` is an explicit re-read. */
export function useYieldCurve() {
  const [asOf, setAsOf] = useState<string | null>(null);
  const [state, setState] = useState<YieldCurveState>({ status: "loading" });
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    getYieldCurve(asOf)
      .then((data) => !cancelled && setState(data ? { status: "ready", data } : { status: "unavailable" }))
      .catch((error: unknown) =>
        !cancelled && setState({ status: "error", message: error instanceof ApiError ? error.message : "ZM Terminal API is unavailable" }));
    return () => { cancelled = true; };
  }, [asOf, reload]);
  const refresh = useCallback(() => setReload((value) => value + 1), []);
  return { state, asOf, setAsOf, refresh };
}
