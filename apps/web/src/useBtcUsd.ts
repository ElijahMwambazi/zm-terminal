import { useCallback, useEffect, useState } from "react";
import { ApiError, getBtcUsdLatest, type BtcUsdQuote } from "./api";

export type BtcUsdState =
  | { status: "loading" }
  | { status: "ready"; quote: BtcUsdQuote }
  | { status: "error"; message: string };

/** One live Bitstamp ticker request per load or explicit refresh; no polling, nothing stored. */
export function useBtcUsd() {
  const [state, setState] = useState<BtcUsdState>({ status: "loading" });
  const load = useCallback(() => {
    let cancelled = false;
    setState({ status: "loading" });
    getBtcUsdLatest()
      .then((quote) => !cancelled && setState({ status: "ready", quote }))
      .catch((error: unknown) =>
        !cancelled && setState({ status: "error", message: error instanceof ApiError ? error.message : "ZM Terminal API is unavailable" }));
    return () => { cancelled = true; };
  }, []);
  useEffect(load, [load]);
  return { state, refresh: load };
}
