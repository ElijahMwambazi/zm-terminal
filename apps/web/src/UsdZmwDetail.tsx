import { forwardRef, useEffect, useState } from "react";
import {
  ApiError, HISTORY_RANGES, getUsdZmwHistory,
  type FxHistoryPoint, type FxQuote, type HistoryRange,
} from "./api";
import { formatLusaka } from "./format";
import PriceChart from "./PriceChart";
import type { QuoteState } from "./useUsdZmwQuote";

type HistoryState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; points: FxHistoryPoint[] };

export function quoteProblem(state: QuoteState): string | null {
  if (state.status !== "error") return null;
  return state.kind === "upstream"
    ? "Bank of Zambia data is currently unavailable. No price is shown."
    : "ZM Terminal API is unavailable. No price is shown.";
}

export function QuoteBlock({ quote }: { quote: FxQuote }) {
  return (
    <dl className="quote-grid">
      <div><dt>MID</dt><dd className="quote-mid">{quote.mid}</dd></div>
      <div><dt>BUY</dt><dd>{quote.buy}</dd></div>
      <div><dt>SELL</dt><dd>{quote.sell}</dd></div>
    </dl>
  );
}

const UsdZmwDetail = forwardRef<HTMLElement, { state: QuoteState; refreshKey: number; onRefresh: () => void }>(
  function UsdZmwDetail({ state, refreshKey, onRefresh }, ref) {
    const [range, setRange] = useState<HistoryRange>("1M");
    const [history, setHistory] = useState<HistoryState>({ status: "loading" });

    // Re-read history after each quote load: a successful quote is persisted by the API.
    const quoteSettled = state.status !== "loading";
    useEffect(() => {
      if (!quoteSettled) return;
      let cancelled = false;
      setHistory({ status: "loading" });
      getUsdZmwHistory(range)
        .then((points) => !cancelled && setHistory({ status: "ready", points }))
        .catch((error: unknown) =>
          !cancelled && setHistory({ status: "error", message: error instanceof ApiError ? error.message : "History unavailable" }));
      return () => { cancelled = true; };
    }, [range, quoteSettled, refreshKey]);

    const problem = quoteProblem(state);
    const quote = state.status === "ready" ? state.quote : null;

    return (
      <article className="panel detail-panel" ref={ref} tabIndex={-1} aria-label="USD/ZMW detail">
        <div className="panel-heading">
          <span>USD/ZMW · ZMW PER USD</span>
          <button type="button" className="link-button" onClick={onRefresh} disabled={state.status === "loading"}>
            {state.status === "loading" ? "LOADING…" : "REFRESH"}
          </button>
        </div>

        <div className="detail-body">
          {state.status === "loading" && <p className="notice">Loading Bank of Zambia quote…</p>}
          {problem && <p className="notice notice--error" role="alert">{problem}</p>}
          {quote && (
            <>
              <QuoteBlock quote={quote} />
              <p className={`badge ${quote.stale ? "badge--stale" : "badge--fresh"}`}>
                {quote.stale ? "STALE — observation older than 96 h" : "FRESH"}
              </p>
              <ul className="provenance">
                <li><span>Source</span><a href={quote.source_url} target="_blank" rel="noreferrer">Bank of Zambia</a> (official published rates)</li>
                <li><span>Observed</span>{formatLusaka(quote.observed_at)}</li>
                <li><span>Retrieved</span>{formatLusaka(quote.retrieved_at)}</li>
              </ul>
            </>
          )}

          <div className="range-row" role="group" aria-label="History range">
            {HISTORY_RANGES.map((item) => (
              <button key={item} type="button" className={item === range ? "range range--active" : "range"}
                aria-pressed={item === range} onClick={() => setRange(item)}>{item}</button>
            ))}
          </div>

          {history.status === "loading" && <p className="notice">Loading history…</p>}
          {history.status === "error" && <p className="notice notice--error" role="alert">{history.message}. No history is shown.</p>}
          {history.status === "ready" && history.points.length === 0 && (
            <p className="notice">No stored observations in this range. History accumulates from quotes this app retrieves.</p>
          )}
          {history.status === "ready" && history.points.length === 1 && (
            <p className="notice">Insufficient history: only one stored observation ({history.points[0].mid} at {formatLusaka(history.points[0].observed_at)}).</p>
          )}
          {history.status === "ready" && history.points.length > 1 && <PriceChart points={history.points} />}
        </div>
      </article>
    );
  },
);

export default UsdZmwDetail;
