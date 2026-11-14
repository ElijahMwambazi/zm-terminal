import { forwardRef, useEffect, useState } from "react";
import { ApiError, COPPER_RANGES, getCopperHistory, type CopperPoint, type CopperRange } from "./api";
import AuctionChart from "./AuctionChart";
import { formatDate, formatFixed, formatLusaka, formatMonth } from "./format";
import type { CopperState } from "./useCopper";

/** Display only: "14507.97363636364" -> "$14,507.97/mt". Stored and API values keep full decimal precision. */
export function perMetricTon(value: string): string {
  return `$${formatFixed(value, 2, true)}/mt`;
}
const chartUsd = (value: number) => `$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
/** Charts do not draw a marker per month beyond this many points (the line and all data are unchanged). */
const MARKER_LIMIT = 250;

type History =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; points: CopperPoint[] };

const CopperDetail = forwardRef<HTMLElement, { state: CopperState; onRefresh: () => void }>(
  function CopperDetail({ state, onRefresh }, ref) {
    const [range, setRange] = useState<CopperRange>("5Y");
    const [history, setHistory] = useState<History>({ status: "loading" });
    useEffect(() => {
      let cancelled = false;
      setHistory({ status: "loading" });
      // Wait for the latest read so mount and refresh issue one history request each (both read storage only).
      if (state.status === "loading") return () => { cancelled = true; };
      getCopperHistory(range)
        .then((points) => !cancelled && setHistory({ status: "ready", points }))
        .catch((error: unknown) =>
          !cancelled && setHistory({ status: "error", message: error instanceof ApiError ? error.message : "History unavailable" }));
      return () => { cancelled = true; };
    }, [range, state]);

    const observation = state.status === "ready" ? state.data.observation : null;

    return (
      <article className="panel detail-panel" ref={ref} tabIndex={-1} aria-label="Global copper benchmark detail">
        <div className="panel-heading">
          <span>COPPER · GLOBAL BENCHMARK <span className="muted">IMF PCPS · MONTHLY AVERAGE</span></span>
          <button type="button" className="link-button" onClick={onRefresh} disabled={state.status === "loading"} title="Re-reads stored data; nothing is fetched from the IMF">
            {state.status === "loading" ? "LOADING…" : "RE-READ STORED"}
          </button>
        </div>
        <div className="detail-body">
          <p className="notice">
            IMF Primary Commodity Prices copper benchmark: one nominal-USD average per calendar month, in US dollars per metric ton. The IMF defines it as copper, grade A cathode, LME spot price, CIF European ports. A global benchmark, not a Zambia copper price, not a live quote and not a futures price. Stored monthly data; local/internal use: public display and redistribution rights are pending.
          </p>
          {state.status === "loading" && <p className="notice">Loading the stored IMF copper benchmark…</p>}
          {state.status === "error" && <p className="notice notice--error" role="alert">{state.message}. No latest value is shown.</p>}
          {state.status === "ready" && !observation && (
            <p className="notice">No stored IMF copper benchmark yet. Import it with <code>python -m app.jobs.backfill_copper --from 1992-01</code>.</p>
          )}
          {observation && (
            <>
              <dl className="quote-grid">
                <div><dt>LATEST STORED MONTH</dt><dd className="quote-mid">{perMetricTon(observation.value)}</dd></div>
                <div><dt>MONTH</dt><dd>{formatMonth(observation.period_start)}</dd></div>
                <div><dt>STATUS</dt><dd>{observation.stale ? <span className="text-stale">Behind schedule</span> : "Latest stored month"}</dd></div>
              </dl>
              <p className={observation.stale ? "notice text-stale" : "notice"}>
                Monthly series, updated by the IMF about once a month. Flagged as behind schedule when the newest stored month is more than {observation.stale_after_months} calendar months before the current month (an application heuristic, not an IMF commitment).
              </p>
              <ul className="provenance">
                <li><span>Source</span><a href={observation.source_page_url} target="_blank" rel="noreferrer">IMF Primary Commodity Prices</a>, series {observation.source_series} (world, US dollars, monthly)</li>
                <li><span>Definition</span>{observation.source_definition} (IMF series description; the IMF is the data provider)</li>
                <li><span>Period</span>{formatDate(observation.period_start)} to {formatDate(observation.period_end)} (monthly average)</li>
                <li><span>Retrieved</span>{formatLusaka(observation.retrieved_at)}</li>
                <li><span>Use</span>Local/internal use. Public display, API redistribution and commercial use are not approved; attribution to the IMF is required.</li>
              </ul>
            </>
          )}
          <section className="history-section" aria-label="Copper benchmark history">
            <div className="panel-heading"><span>COPPER · MONTHLY HISTORY (STORED)</span></div>
            <div className="range-row" role="group" aria-label="History range">
              {COPPER_RANGES.map((item) => (
                <button key={item} type="button" className={item === range ? "range range--active" : "range"}
                  aria-pressed={item === range} onClick={() => setRange(item)}>{item}</button>
              ))}
            </div>
            {history.status === "loading" && <p className="notice">Loading stored history…</p>}
            {history.status === "error" && <p className="notice notice--error" role="alert">{history.message}. No history is shown.</p>}
            {history.status === "ready" && history.points.length === 0 && (
              <p className="notice">No stored copper months in this range. Months the IMF did not publish are never filled in.</p>
            )}
            {history.status === "ready" && history.points.length === 1 && (
              <p className="notice">Only one stored month in this range: {perMetricTon(history.points[0].value)} in {formatMonth(history.points[0].period_start)}.</p>
            )}
            {history.status === "ready" && history.points.length > 1 && (
              <>
                <AuctionChart
                  points={history.points.map((p) => ({ auction_date: p.period_start, yield_rate: p.value }))}
                  label="Copper monthly average history, USD per metric ton"
                  precision={2} formatPrice={chartUsd} monthly markers={history.points.length <= MARKER_LIMIT} />
                <p className="notice">
                  {history.points.length} stored months, {formatMonth(history.points[0].period_start)} to {formatMonth(history.points[history.points.length - 1].period_start)}. Each point is one published monthly average; the line joins published observations only and does not imply interpolated values. Months the IMF did not publish are never filled in.
                </p>
              </>
            )}
          </section>
        </div>
      </article>
    );
  },
);

export default CopperDetail;
