import { forwardRef, useEffect, useState } from "react";
import {
  ApiError, OIL_BENCHMARKS, OIL_RANGES, getOilHistory,
  type OilBenchmark, type OilObservation, type OilPoint, type OilRange,
} from "./api";
import AuctionChart from "./AuctionChart";
import { formatDate, formatLusaka } from "./format";
import type { OilState } from "./useOil";

export const perBarrel = (value: string) => `${value.startsWith("-") ? "-" : ""}$${value.replace("-", "")}/bbl`;
const formatChartPrice = (value: number) => `${value < 0 ? "-" : ""}$${Math.abs(value).toFixed(2)}`;

type History =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; points: OilPoint[] };

const OilDetail = forwardRef<HTMLElement, { state: OilState; focus: OilBenchmark | null; onRefresh: () => void }>(
  function OilDetail({ state, focus, onRefresh }, ref) {
    const [benchmark, setBenchmark] = useState<OilBenchmark>(focus ?? "BRENT");
    const [range, setRange] = useState<OilRange>("1Y");
    const [history, setHistory] = useState<History>({ status: "loading" });
    useEffect(() => { if (focus) setBenchmark(focus); }, [focus]);
    useEffect(() => {
      let cancelled = false;
      setHistory({ status: "loading" });
      getOilHistory(benchmark, range)
        .then((points) => !cancelled && setHistory({ status: "ready", points }))
        .catch((error: unknown) =>
          !cancelled && setHistory({ status: "error", message: error instanceof ApiError ? error.message : "History unavailable" }));
      return () => { cancelled = true; };
    }, [benchmark, range, state]);

    const data = state.status === "ready" ? state.data : null;
    const byBenchmark = new Map<OilBenchmark, OilObservation>((data?.observations ?? []).map((o) => [o.benchmark, o]));
    const selected = byBenchmark.get(benchmark);

    return (
      <article className="panel detail-panel" ref={ref} tabIndex={-1} aria-label="Global oil benchmarks detail">
        <div className="panel-heading">
          <span>GLOBAL CRUDE-OIL BENCHMARKS · USD PER BARREL</span>
          <button type="button" className="link-button" onClick={onRefresh} disabled={state.status === "loading"}>
            {state.status === "loading" ? "LOADING…" : "REFRESH"}
          </button>
        </div>
        <div className="detail-body">
          <p className="notice">Global benchmarks, not Zambian prices. Not converted to ZMW and not related to Zambian pump prices.</p>
          {state.status === "loading" && <p className="notice">Loading the latest published benchmark values…</p>}
          {state.status === "error" && <p className="notice notice--error" role="alert">{state.message}. No latest value is shown.</p>}
          {data && (
            <>
              <div className="table-scroll">
                <table>
                  <thead><tr><th>BENCHMARK</th><th>VALUE</th><th>UNIT</th><th>OBSERVED</th><th>STATUS</th></tr></thead>
                  <tbody>
                    {OIL_BENCHMARKS.map((b) => {
                      const row = byBenchmark.get(b);
                      return (
                        <tr key={b} className={focus === b ? "row--focus" : undefined}>
                          <td>{b}{row && <><br /><small className="muted">{row.name}</small></>}</td>
                          <td>{row ? perBarrel(row.value) : "—"}</td>
                          <td>{row ? "USD per barrel" : "—"}</td>
                          <td>{row ? formatDate(row.observed_date) : "No recent observation"}</td>
                          <td>{row ? (row.stale ? <span className="text-stale">Stale</span> : "Recent") : "—"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {data.observations.some((o) => o.stale) && (
                <p className="notice text-stale">Stale: the newest published observation is more than 10 days old (application heuristic).</p>
              )}
              <p className="notice">Daily values for trading days only. Weekends and holidays have no observation and are never filled in. The source publishes with a lag of days, so the latest value is not a live quote.</p>
              {selected && (
                <ul className="provenance">
                  <li><span>Source</span><a href={selected.source_url} target="_blank" rel="noreferrer">FRED series {selected.series_id}</a> (U.S. Energy Information Administration data via OpenBB)</li>
                  <li><span>Retrieved</span>{formatLusaka(selected.retrieved_at)}</li>
                  <li><span>Note</span>Observation dates are trading days, not times. Source terms are pending review.</li>
                </ul>
              )}
            </>
          )}
          <section className="history-section" aria-label="Oil benchmark history">
            <div className="panel-heading"><span>{benchmark} · DAILY HISTORY (STORED)</span></div>
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
              <p className="notice">No stored {benchmark} observations in this range. Import history with <code>python -m app.jobs.backfill_oil --from 2025-01-01</code>.</p>
            )}
            {history.status === "ready" && history.points.length === 1 && (
              <p className="notice">Only one stored observation in this range: {perBarrel(history.points[0].value)} on {formatDate(history.points[0].observed_date)}.</p>
            )}
            {history.status === "ready" && history.points.length > 1 && (
              <>
                <AuctionChart
                  points={history.points.map((p) => ({ auction_date: p.observed_date, yield_rate: p.value }))}
                  label={`${benchmark} crude oil history, USD per barrel`}
                  precision={2} formatPrice={formatChartPrice} />
                <p className="notice">{history.points.length} stored observations, {formatDate(history.points[0].observed_date)} to {formatDate(history.points[history.points.length - 1].observed_date)}. Each marker is a published value; lines only join markers and do not imply values on days without one. USD per barrel · Source: FRED.</p>
              </>
            )}
          </section>
        </div>
      </article>
    );
  },
);

export default OilDetail;
