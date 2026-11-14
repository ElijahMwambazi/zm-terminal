import { forwardRef, useEffect, useState } from "react";
import { ApiError, OIL_ZM_RANGES, getOilZmHistory, type OilRange, type OilZmHistory, type OilZmMonth } from "./api";
import AuctionChart from "./AuctionChart";
import { formatDate, formatFixed, formatLusaka, formatMonth } from "./format";
import type { OilZmState } from "./useOilZm";

const zmw = (value: string) => `K${formatFixed(value, 2, true)}`;
const usd = (value: string) => `$${formatFixed(value, 2, true)}`;
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
const chartZmw = (value: number) => `K${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
/** Charts do not draw a marker per month beyond this many points (the line and all data are unchanged). */
const MARKER_LIMIT = 250;

type History =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: OilZmHistory };

function Calculation({ m }: { m: OilZmMonth }) {
  return (
    <>
      <div className="derived-value" aria-label="Derived ZMW per barrel">
        {zmw(m.zmw_per_bbl)}<span className="derived-unit"> / bbl</span>
        <span className="badge-derived" title="ZM Terminal derived indicator, not an official series">DERIVED</span>
      </div>
      <p className="derived-calc">
        {usd(m.brent_usd_per_bbl)} Brent × {formatFixed(m.usd_zmw_observed_days_avg, 4)} ZMW/USD observed-days average = {zmw(m.zmw_per_bbl)}/bbl
        <small className="muted"> · shown rounded; computed from unrounded values</small>
      </p>
      <p className="derived-calc derived-support" aria-label="FX data support">
        Based on {plural(m.fx_day_count, "stored BoZ day")} · {plural(m.fx_observation_count, "observation")}
      </p>
    </>
  );
}

const OilZmDetail = forwardRef<HTMLElement, { state: OilZmState; onRefresh: () => void }>(
  function OilZmDetail({ state, onRefresh }, ref) {
    const [range, setRange] = useState<OilRange>("1Y");
    const [history, setHistory] = useState<History>({ status: "loading" });
    useEffect(() => {
      let cancelled = false;
      setHistory({ status: "loading" });
      if (state.status === "loading") return () => { cancelled = true; }; // one history request per load or refresh
      getOilZmHistory(range)
        .then((data) => !cancelled && setHistory({ status: "ready", data }))
        .catch((error: unknown) =>
          !cancelled && setHistory({ status: "error", message: error instanceof ApiError ? error.message : "History unavailable" }));
      return () => { cancelled = true; };
    }, [range, state]);

    const m = state.status === "ready" ? state.data : null;
    const points = history.status === "ready" ? history.data.observations : [];
    const partialMonths = points.filter((p) => p.fx_coverage === "partial_source_window");

    return (
      <article className="panel detail-panel" ref={ref} tabIndex={-1} aria-label="OIL ZM derived monthly benchmark">
        <div className="panel-heading">
          <span>OIL ZM · DERIVED MONTHLY BENCHMARK</span>
          <button type="button" className="link-button" onClick={onRefresh} disabled={state.status === "loading"}>
            {state.status === "loading" ? "LOADING…" : "REFRESH"}
          </button>
        </div>
        <div className="detail-body">
          <p className="notice notice--derived">
            {m?.notice ?? "Derived analytical value. Not a Zambia retail fuel price or an official published series."} A monthly average, not a live quote, not a forecast.
          </p>
          {state.status === "loading" && <p className="notice">Loading stored Brent and BoZ data…</p>}
          {state.status === "error" && <p className="notice notice--error" role="alert">{state.message}. No derived value is shown.</p>}
          {state.status === "unavailable" && (
            <p className="notice">OIL ZM is not available yet: no month has both a stored World Bank Brent average and stored BoZ USD/ZMW observations. Import Brent with <code>python -m app.jobs.backfill_oil --from 2025-01</code> and FX with <code>python -m app.jobs.backfill_usdzmw --from 2026-01-22</code>.</p>
          )}
          {m && (
            <>
              <Calculation m={m} />
              {m.stale && <p className="notice text-stale">Behind schedule: the newest derived month is more than {m.stale_after_months} calendar months before the current month (heuristic).</p>}
              {m.fx_coverage === "partial_source_window" && (
                <p className="notice text-stale">
                  Partial source window: stored BoZ history begins on {formatDate(m.fx_first_date)}, after the first day of {formatMonth(m.period_start)}, so the observed-days average covers only the part of the month from that date.
                </p>
              )}
              <ul className="provenance components" aria-label="Calculation components">
                <li><span>Source month</span>{formatMonth(m.period_start)} ({formatDate(m.period_start)} to {formatDate(m.period_end)})</li>
                <li><span>Brent</span>{usd(m.brent_usd_per_bbl)}/bbl · World Bank monthly average</li>
                <li><span>BoZ USD/ZMW observed-days average</span>{formatFixed(m.usd_zmw_observed_days_avg, 4)} ZMW/USD</li>
                <li><small className="muted">Mean of daily mid-rate averages across stored BoZ observation days, each day weighted equally. Days without a stored observation are not filled, and no coverage of the calendar month is asserted.</small></li>
                <li><span>Stored BoZ days</span>{m.fx_day_count}</li>
                <li><span>Observations</span>{m.fx_observation_count}</li>
                <li><span>First / last observation</span>{formatDate(m.fx_first_date)} to {formatDate(m.fx_last_date)}</li>
              </ul>
              <ul className="provenance">
                <li><span>Method</span>{m.methodology} ({m.methodology_id})</li>
                <li><span>Brent</span><a href={m.brent_source_page_url} target="_blank" rel="noreferrer">World Bank Pink Sheet</a>, published {formatDate(m.brent_publication_date)}</li>
                <li><span>FX</span>Bank of Zambia {m.fx_pair} {m.fx_price}, stored official-slot observations, {formatLusaka(m.fx_first_observation)} to {formatLusaka(m.fx_last_observation)}</li>
                <li><span>Licence</span>Inherits the World Bank Brent input: catalog licence CC BY 4.0 · workbook applicability under review</li>
              </ul>
            </>
          )}
          <section className="history-section" aria-label="OIL ZM history">
            <div className="panel-heading"><span>OIL ZM · DERIVED MONTHLY HISTORY</span></div>
            <div className="range-row" role="group" aria-label="History range">
              {OIL_ZM_RANGES.map((item) => (
                <button key={item} type="button" className={item === range ? "range range--active" : "range"}
                  aria-pressed={item === range} onClick={() => setRange(item)}>{item}</button>
              ))}
            </div>
            {history.status === "loading" && <p className="notice">Loading derived history…</p>}
            {history.status === "error" && <p className="notice notice--error" role="alert">{history.message}. No history is shown.</p>}
            {history.status === "ready" && points.length === 0 && (
              <p className="notice">No derived months in this range: a month needs both a stored Brent average and stored BoZ USD/ZMW data.</p>
            )}
            {history.status === "ready" && points.length === 1 && (
              <p className="notice">Only one derived month in this range: {zmw(points[0].zmw_per_bbl)}/bbl in {formatMonth(points[0].period_start)}.</p>
            )}
            {history.status === "ready" && points.length > 1 && (
              <AuctionChart
                points={points.map((p) => ({ auction_date: p.period_start, yield_rate: p.zmw_per_bbl }))}
                label="OIL ZM derived monthly ZMW per barrel" precision={2} formatPrice={chartZmw} monthly
                markers={points.length <= MARKER_LIMIT} />
            )}
            {history.status === "ready" && points.length > 0 && (
              <>
                <div className="table-scroll">
                  <table>
                    <thead><tr><th>MONTH</th><th>BRENT $/bbl</th><th>USD/ZMW OBS-DAYS AVG</th><th>ZMW/bbl</th><th>BOZ DAYS</th><th>OBSERVATIONS</th></tr></thead>
                    <tbody>
                      {points.map((p) => (
                        <tr key={p.period_start}>
                          <td>{formatMonth(p.period_start)}{p.fx_coverage === "partial_source_window" && <span className="text-stale"> · partial</span>}</td>
                          <td>{formatFixed(p.brent_usd_per_bbl, 2)}</td><td>{formatFixed(p.usd_zmw_observed_days_avg, 4)}</td>
                          <td>{formatFixed(p.zmw_per_bbl, 2, true)}</td><td>{p.fx_day_count}</td><td>{p.fx_observation_count}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="notice">
                  {points.length} derived months, {formatMonth(points[0].period_start)} to {formatMonth(points[points.length - 1].period_start)}. One point per month, plotted at the start of the month; the line only joins derived months and does not imply values for months without both inputs.
                  {history.status === "ready" && history.data.brent_months_without_fx > 0 && <> {history.data.brent_months_without_fx} stored Brent month(s) in this range have no stored BoZ USD/ZMW and are omitted (BoZ machine-readable history begins on 22 Jan 2026).</>}
                  {partialMonths.length > 0 && <> Months marked partial begin where the stored BoZ history begins.</>}
                  {" "}Sparse months stay derivable: the BOZ DAYS and OBSERVATIONS columns show how much stored BoZ data supports each value, and no business-day completeness is claimed.
                </p>
              </>
            )}
          </section>
        </div>
      </article>
    );
  },
);

export default OilZmDetail;
