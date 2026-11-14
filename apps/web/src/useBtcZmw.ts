import { useCallback, useEffect, useState } from "react";
import { ApiError, getBtcZmwLatest, type BtcZmwQuote } from "./api";

export type BtcZmwState =
  | { status: "loading" }
  | { status: "ready"; quote: BtcZmwQuote }
  | { status: "error"; message: string };

/** Live Bitstamp BTC/USD x the latest STORED BoZ mid, computed by the API; one request per load or explicit refresh. */
export function useBtcZmw() {
  const [state, setState] = useState<BtcZmwState>({ status: "loading" });
  const load = useCallback(() => {
    let cancelled = false;
    setState({ status: "loading" });
    getBtcZmwLatest()
      .then((quote) => !cancelled && setState({ status: "ready", quote }))
      .catch((error: unknown) =>
        !cancelled && setState({ status: "error", message: error instanceof ApiError ? error.message : "ZM Terminal API is unavailable" }));
    return () => { cancelled = true; };
  }, []);
  useEffect(load, [load]);
  return { state, refresh: load };
}
