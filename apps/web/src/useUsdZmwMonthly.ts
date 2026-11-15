import { useEffect, useState } from "react";
import { ApiError, getUsdZmwMonthly, type FxMonthlyHistory, type MonthlyRange } from "./api";

export type MonthlyState =
  | { status: "loading"; previous: FxMonthlyHistory | null }
  | { status: "ready"; data: FxMonthlyHistory }
  | { status: "error"; message: string };

/** Stored BoZ monthly averages for a range. While a new range loads, the previous data is kept so the layout does not jump. */
export function useUsdZmwMonthly(range: MonthlyRange): MonthlyState {
  const [state, setState] = useState<MonthlyState>({ status: "loading", previous: null });
  useEffect(() => {
    let cancelled = false;
    setState((current) => ({
      status: "loading",
      previous: current.status === "ready" ? current.data : current.status === "loading" ? current.previous : null,
    }));
    getUsdZmwMonthly(range)
      .then((data) => !cancelled && setState({ status: "ready", data }))
      .catch((error: unknown) =>
        !cancelled && setState({ status: "error", message: error instanceof ApiError ? error.message : "Unexpected error" }));
    return () => { cancelled = true; };
  }, [range]);
  return state;
}
