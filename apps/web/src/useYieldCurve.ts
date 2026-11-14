import { useCallback, useEffect, useState } from "react";
import { ApiError, getYieldCurve, getYieldSpreads, type YieldCurve, type YieldSpreads } from "./api";

export type YieldCurveState =
  | { status: "loading" }
  | { status: "ready"; data: YieldCurve }
  | { status: "unavailable" } // no stored accepted observation on or before the date
  | { status: "error"; message: string };

export type YieldSpreadsState =
  | { status: "loading" }
  | { status: "ready"; data: YieldSpreads }
  | { status: "unavailable" }
  | { status: "error"; message: string };

/** Reads the derived snapshot from the API (stored data only). asOf null means latest; `refresh` is an explicit re-read. */
export function useYieldCurve() {
  const [asOf, setAsOf] = useState<string | null>(null);
  const [state, setState] = useState<YieldCurveState>({ status: "loading" });
  const [spreadState, setSpreadState] = useState<YieldSpreadsState>({ status: "loading" });
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    setSpreadState({ status: "loading" });
    const message = (error: unknown) => (error instanceof ApiError ? error.message : "ZM Terminal API is unavailable");
    getYieldCurve(asOf)
      .then((data) => {
        if (cancelled) return;
        setState(data ? { status: "ready", data } : { status: "unavailable" });
        if (!data) return setSpreadState({ status: "unavailable" });
        // Spreads are requested for the curve's own as_of date, so both always describe one snapshot (also across midnight).
        return getYieldSpreads(data.as_of)
          .then((spreads) => !cancelled && setSpreadState(spreads ? { status: "ready", data: spreads } : { status: "unavailable" }))
          .catch((error: unknown) => !cancelled && setSpreadState({ status: "error", message: message(error) }));
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState({ status: "error", message: message(error) });
        setSpreadState({ status: "error", message: message(error) });
      });
    return () => { cancelled = true; };
  }, [asOf, reload]);
  const refresh = useCallback(() => setReload((value) => value + 1), []);
  return { state, spreadState, asOf, setAsOf, refresh };
}
