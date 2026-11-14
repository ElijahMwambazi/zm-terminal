import { forwardRef, useEffect, useState } from "react";
import {
  ApiError, OIL_BENCHMARKS, OIL_RANGES, getOilHistory,
  type OilBenchmark, type OilObservation, type OilPoint, type OilRange,
} from "./api";
import AuctionChart from "./AuctionChart";
import { formatDate, formatLusaka, formatMonth } from "./format";
import type { OilState } from "./useOil";

/** Display only: exactly two decimals ("79.1" -> "$79.10/bbl", "-37.63" -> "-$37.63/bbl"). Stored precision is untouched. */
export function perBarrel(value: string): string {
  const negative = value.startsWith("-");
  const digits = negative ? value.slice(1) : value;
  const [whole, fraction = ""] = digits.split(".");
  // Plain decimal strings with at most two decimals are padded exactly; anything longer is rounded for display only.
  const shown = /^\d+$/.test(whole) && fraction.length <= 2 && /^\d*$/.test(fraction)
    ? `${whole}.${fraction.padEnd(2, "0")}`
    : Math.abs(Number(value)).toFixed(2);
  return `${negative ? "-" : ""}$${shown}/bbl`;
}
/** Charts do not draw a marker per observation beyond this many points (the line and all data are unchanged). */
const MARKER_LIMIT = 250;
const formatChartPrice = (value: number) => `${value < 0 ? "-" : ""}$${Math.abs(value).toFixed(2)}`;

type History =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; points: OilPoint[] };

const OilDetail = forwardRef<HTMLElement, { state: OilState; focus: OilBenchmark | null; navigationKey: number; onRefresh: () => void }>(
  function OilDetail({ state, focus, navigationKey, onRefresh }, ref) {
    const [benchmark, setBenchmark] = useState<OilBenchmark>(focus ?? "BRENT");
    const [range, setRange] = useState<OilRange>("5Y");
    const [history, setHistory] = useState<History>({ status: "loading" });
    // navigationKey changes on every OIL command, so a repeated command re-selects even if focus is unchanged.
    useEffect(() => { if (focus) setBenchmark(focus); }, [focus, navigationKey]);
    useEffect(() => {
      let cancelled = false;
      setHistory({ status: "loading" });
      // Wait for the latest read so mount and refresh issue one history request each.
      if (state.status === "loading") return () => { cancelled = true; };
      getOilHistory(benchmark, range)
        .then((points) => !cancelled && setHistory({ status: "ready", points }))
        .catch((error: unknown) =>
          !cancelled && setHistory({ status: "error", message: error instanceof ApiError ? error.message : "History unavailable" }));
      return () => { cancelled = true; };
    }, [benchmark, range, state]);

    const data = state.status === "ready" ? state.data : null;
    const byBenchmark = new Map<OilBenchmark, OilObservation>((data?.observations ?? []).map((o) => [o.benchmark, o]));
    const selected = byBenchmark.get(benchmark);
    const staleMonths = data?.observations[0]?.stale_after_months ?? null;

    return (
      <article className="panel detail-panel" ref={ref} tabIndex={-1} aria-label="Global oil benchmarks detail">
        <div className="panel-heading">
          <span>GLOBAL OIL · MONTHLY BENCHMARKS</span>
          <button type="button" className="link-button" onClick={onRefresh} disabled={state.status === "loading"}>
            {state.status === "loading" ? "LOADING…" : "REFRESH"}
          </button>
        </div>
        <div className="detail-body">
          <p className="notice">World Bank Pink Sheet monthly commodity price: one average per calendar month, in USD per barrel. Global benchmarks, not live or daily spot prices, not converted to ZMW and not Zambian pump prices.</p>
          {state.status === "loading" && <p className="notice">Loading stored World Bank prices…</p>}
          {state.status === "error" && <p className="notice notice--error" role="alert">{state.message}. No latest value is shown.</p>}
          {data && (
            <>
              <div className="table-scroll">
                <table>
                  <thead><tr><th>BENCHMARK</th><th>VALUE</th><th>UNIT</th><th>MONTH</th><th>STATUS</th></tr></thead>
                  <tbody>
                    {OIL_BENCHMARKS.map((b) => {
                      const row = byBenchmark.get(b);
                      return (
                        <tr key={b} className={focus === b ? "row--focus" : undefined}>
                          <td>{b}{row && <><br /><small className="muted">{row.description}</small></>}</td>
                          <td>{row ? perBarrel(row.value) : "—"}</td>
                          <td>{row ? "USD/bbl" : "—"}</td>
                          <td>{row ? formatMonth(row.period_start) : "No stored month"}</td>
                          <td>{row ? (row.stale ? <span className="text-stale">Behind schedule</span> : "Latest month") : "—"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {data.observations.length === 0 && (
                <p className="notice">No stored World Bank oil prices yet. Import them with <code>python -m app.jobs.backfill_oil --from 2025-01</code>.</p>
              )}
              {staleMonths !== null && (
                <p className={data.observations.some((o) => o.stale) ? "notice text-stale" : "notice"}>
                  Monthly series, updated by the World Bank about once a month. Flagged as behind schedule when the newest stored month is more than {staleMonths} calendar months before the current month; this is a heuristic, and the newest month is shown regardless.
                </p>
              )}
              {selected && (
                <ul className="provenance">
                  <li><span>Source</span><a href={selected.source_url} target="_blank" rel="noreferrer">World Bank Commodity Price Data (The Pink Sheet)</a>, series “{selected.source_series}” · CC BY 4.0</li>
                  <li><span>Period</span>{formatDate(selected.period_start)} to {formatDate(selected.period_end)} (monthly average)</li>
                  <li><span>Published</span>{formatDate(selected.publication_date)} (workbook release)</li>
                  <li><span>Retrieved</span>{formatLusaka(selected.retrieved_at)}</li>
                </ul>
              )}
            </>
          )}
          <section className="history-section" aria-label="Oil benchmark history">
            <div className="panel-heading"><span>{benchmark} · MONTHLY HISTORY (STORED)</span></div>
            <div className="range-row" role="group" aria-label="Oil benchmark">
              {OIL_BENCHMARKS.map((item) => (
                <button key={item} type="button" className={item === benchmark ? "range range--active" : "range"}
                  aria-pressed={item === benchmark} onClick={() => setBenchmark(item)}>{item}</button>
              ))}
            </div>
            <div className="range-row" role="group" aria-label="History range">
              {OIL_RANGES.map((item) => (
                <button key={item} type="button" className={item === range ? "range range--active" : "range"}
                  aria-pressed={item === range} onClick={() => setRange(item)}>{item}</button>
              ))}
            </div>
            {history.status === "loading" && <p className="notice">Loading stored history…</p>}
            {history.status === "error" && <p className="notice notice--error" role="alert">{history.message}. No history is shown.</p>}
            {history.status === "ready" && history.points.length === 0 && (
              <p className="notice">No stored {benchmark} months in this range. Months the World Bank did not publish are never filled in.</p>
            )}
            {history.status === "ready" && history.points.length === 1 && (
              <p className="notice">Only one stored month in this range: {perBarrel(history.points[0].value)} in {formatMonth(history.points[0].period_start)}.</p>
            )}
            {history.status === "ready" && history.points.length > 1 && (
              <>
                <AuctionChart
                  points={history.points.map((p) => ({ auction_date: p.period_start, yield_rate: p.value }))}
                  label={`${benchmark} crude oil monthly average history, USD per barrel`}
                  precision={2} formatPrice={formatChartPrice} monthly markers={history.points.length <= MARKER_LIMIT} />
                <p className="notice">{history.points.length} stored months, {formatMonth(history.points[0].period_start)} to {formatMonth(history.points[history.points.length - 1].period_start)}. Each point is one published monthly average, plotted at the start of its month; the line only joins published months and does not imply values for months without one. USD per barrel · Source: World Bank Pink Sheet (CC BY 4.0).</p>
              </>
            )}
          </section>
        </div>
      </article>
    );
  },
);

export default OilDetail;
