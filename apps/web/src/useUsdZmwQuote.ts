import { useCallback, useEffect, useState } from "react";
import { ApiError, getUsdZmwQuote, type FxQuote } from "./api";

export type QuoteState =
  | { status: "loading" }
  | { status: "ready"; quote: FxQuote }
  | { status: "error"; kind: "upstream" | "api" };

/** Loads once on mount; `refresh` is an explicit user action (one BoZ request each). */
export function useUsdZmwQuote() {
  const [state, setState] = useState<QuoteState>({ status: "loading" });
  const [refreshKey, setRefreshKey] = useState(0);

  const load = useCallback(() => {
    setState({ status: "loading" });
    getUsdZmwQuote()
      .then((quote) => setState({ status: "ready", quote }))
      .catch((error: unknown) =>
        setState({ status: "error", kind: error instanceof ApiError ? error.kind : "api" }))
      .finally(() => setRefreshKey((key) => key + 1));
  }, []);

  useEffect(load, [load]);
  return { state, refreshKey, refresh: load };
}
