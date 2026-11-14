import { forwardRef, useEffect, useState } from "react";
import { ApiError, COPPER_RANGES, getCopperZmHistory, type CopperRange, type CopperZmHistory, type CopperZmMonth } from "./api";
import AuctionChart from "./AuctionChart";
import { formatDate, formatFixed, formatLusaka, formatMonth } from "./format";
import type { CopperZmState } from "./useCopperZm";

export const zmwPerMetricTon = (value: string) => `K${formatFixed(value, 2, true)}/mt`;
const zmw = (value: string) => `K${formatFixed(value, 2, true)}`;
const usd = (value: string) => `$${formatFixed(value, 2, true)}`;
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
const chartZmw = (value: number) => `K${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const MARKER_LIMIT = 250;
const FALLBACK_NOTICE = "Derived analytical value. Not an official IMF, LME or Bank of Zambia series, not a Zambia copper price and not an executable quote.";

type History =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: CopperZmHistory };

function Calculation({ m }: { m: CopperZmMonth }) {
  return (
    <>
      <div className="derived-value" aria-label="Derived ZMW per metric ton">
        {zmw(m.zmw_per_mt)}<span className="derived-unit"> / mt</span>
        <span className="badge-derived" title="ZM Terminal derived indicator, not an official series">DERIVED</span>
      </div>
      <p className="derived-calc">
        {usd(m.copper_usd_per_mt)} Copper × {formatFixed(m.usd_zmw_observed_days_avg, 4)} ZMW/USD observed-days average = {zmw(m.zmw_per_mt)}/mt
        <small className="muted"> · shown rounded; computed from unrounded values</small>
      </p>
      <p className="derived-calc derived-support" aria-label="FX data support">
        Based on {plural(m.fx_day_count, "stored BoZ day")} · {plural(m.fx_observation_count, "observation")}
      </p>
    </>
  );
}

const CopperZmDetail = forwardRef<HTMLElement, { state: CopperZmState; onRefresh: () => void }>(
  function CopperZmDetail({ state, onRefresh }, ref) {
    const [range, setRange] = useState<CopperRange>("1Y");
    const [history, setHistory] = useState<History>({ status: "loading" });
    useEffect(() => {
      let cancelled = false;
      setHistory({ status: "loading" });
      if (state.status === "loading") return () => { cancelled = true; }; // one history request per load or refresh
      getCopperZmHistory(range)
        .then((data) => !cancelled && setHistory({ status: "ready", data }))
        .catch((error: unknown) =>
          !cancelled && setHistory({ status: "error", message: error instanceof ApiError ? error.message : "History unavailable" }));
      return () => { cancelled = true; };
    }, [range, state]);

    const m = state.status === "ready" ? state.data : null;
    const points = history.status === "ready" ? history.data.observations : [];
    const partialMonths = points.filter((p) => p.fx_coverage === "partial_source_window");

    return (
      <article className="panel detail-panel" ref={ref} tabIndex={-1} aria-label="COPPER ZM derived monthly benchmark">
        <div className="panel-heading">
          <span>COPPER ZM · DERIVED MONTHLY BENCHMARK</span>
          <button type="button" className="link-button" onClick={onRefresh} disabled={state.status === "loading"}>
            {state.status === "loading" ? "LOADING…" : "RE-READ STORED"}
          </button>
        </div>
        <div className="detail-body">
          <p className="notice notice--derived">
            {m?.notice ?? FALLBACK_NOTICE} A ZM Terminal calculation that converts the global IMF Copper benchmark with stored BoZ FX: not Zambia's actual copper sale or export price, not live, not executable and not a forecast.
          </p>
          {m && <p className="notice">{m.timing_note}</p>}
          <p className="notice">Local/internal use: a derived analytical value whose public display, API redistribution and commercial use remain under review.</p>
          {state.status === "loading" && <p className="notice">Loading stored Copper and BoZ data…</p>}
          {state.status === "error" && <p className="notice notice--error" role="alert">{state.message}. No derived value is shown.</p>}
          {state.status === "unavailable" && (
            <p className="notice">COPPER ZM is not available yet: no month has both a stored IMF Copper monthly average and stored BoZ USD/ZMW observations. Import Copper with <code>python -m app.jobs.backfill_copper --from 1992-01</code> and FX with <code>python -m app.jobs.backfill_usdzmw --from 2026-01-22</code>.</p>
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
                <li><span>Copper</span>{usd(m.copper_usd_per_mt)}/mt · IMF PCPS · {m.copper_source_series} · monthly average</li>
                <li><span>BoZ USD/ZMW observed-days average</span>{formatFixed(m.usd_zmw_observed_days_avg, 4)} ZMW/USD</li>
                <li><small className="muted">Mean of daily mid-rate averages across stored BoZ observation days, each day weighted equally. Days without a stored observation are omitted and not filled, and no business-day or calendar-month completeness is claimed.</small></li>
                <li><span>Stored BoZ days</span>{m.fx_day_count}</li>
                <li><span>Observations</span>{m.fx_observation_count}</li>
                <li><span>First / last FX day used</span>{formatDate(m.fx_first_date)} to {formatDate(m.fx_last_date)}</li>
              </ul>
              <ul className="provenance">
                <li><span>Method</span>{m.methodology} ({m.methodology_id})</li>
                <li><span>Copper</span><a href={m.copper_source_page_url} target="_blank" rel="noreferrer">IMF Primary Commodity Prices</a> · {m.copper_source_definition}</li>
                <li><span>FX</span>Bank of Zambia {m.fx_pair} {m.fx_price}, stored official-slot observations, {formatLusaka(m.fx_first_observation)} to {formatLusaka(m.fx_last_observation)}</li>
              </ul>
            </>
          )}
          <section className="history-section" aria-label="COPPER ZM history">
            <div className="panel-heading"><span>COPPER ZM · DERIVED MONTHLY HISTORY</span></div>
            <div className="range-row" role="group" aria-label="History range">
              {COPPER_RANGES.map((item) => (
                <button key={item} type="button" className={item === range ? "range range--active" : "range"}
                  aria-pressed={item === range} onClick={() => setRange(item)}>{item}</button>
              ))}
            </div>
            {history.status === "loading" && <p className="notice">Loading derived history…</p>}
            {history.status === "error" && <p className="notice notice--error" role="alert">{history.message}. No history is shown.</p>}
            {history.status === "ready" && points.length === 0 && (
              <p className="notice">No derived months in this range: a month needs both a stored IMF Copper average and stored BoZ USD/ZMW data.
                {history.data.copper_months_without_fx > 0 && <> {history.data.copper_months_without_fx} stored Copper month(s) in this range have no stored BoZ USD/ZMW and are omitted.</>}</p>
            )}
            {history.status === "ready" && points.length === 1 && (
              <p className="notice">Only one derived month in this range: {zmwPerMetricTon(points[0].zmw_per_mt)} in {formatMonth(points[0].period_start)}.</p>
            )}
            {history.status === "ready" && points.length > 1 && (
              <AuctionChart
                points={points.map((p) => ({ auction_date: p.period_start, yield_rate: p.zmw_per_mt }))}
                label="COPPER ZM derived monthly ZMW per metric ton" precision={2} formatPrice={chartZmw} monthly
                markers={points.length <= MARKER_LIMIT} />
            )}
            {history.status === "ready" && points.length > 0 && (
              <>
                <div className="table-scroll">
                  <table>
                    <thead><tr><th>MONTH</th><th>COPPER $/mt</th><th>USD/ZMW OBS-DAYS AVG</th><th>ZMW/mt</th><th>BOZ DAYS</th><th>OBSERVATIONS</th></tr></thead>
                    <tbody>
                      {points.map((p) => (
                        <tr key={p.period_start}>
                          <td>{formatMonth(p.period_start)}{p.fx_coverage === "partial_source_window" && <span className="text-stale"> · partial</span>}</td>
                          <td>{formatFixed(p.copper_usd_per_mt, 2, true)}</td><td>{formatFixed(p.usd_zmw_observed_days_avg, 4)}</td>
                          <td>{formatFixed(p.zmw_per_mt, 2, true)}</td><td>{p.fx_day_count}</td><td>{p.fx_observation_count}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="notice">
                  {points.length} derived months, {formatMonth(points[0].period_start)} to {formatMonth(points[points.length - 1].period_start)}. One point per derivable calendar month, plotted at the start of the month; only months with both stored inputs appear and no values are filled. The line only joins derived months and does not imply values for months without both inputs.
                  {history.status === "ready" && history.data.copper_months_without_fx > 0 && <> {history.data.copper_months_without_fx} stored Copper month(s) in this range have no stored BoZ USD/ZMW and are omitted.</>}
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

export default CopperZmDetail;
