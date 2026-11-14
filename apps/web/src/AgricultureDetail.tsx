import { forwardRef, useEffect, useState } from "react";
import {
  AGRICULTURE_COMMODITIES, AGRICULTURE_LABELS, AGRICULTURE_RANGES, ApiError,
  getAgricultureHistory,
  type AgricultureCommodity, type AgricultureHistoryPoint, type AgricultureRange,
} from "./api";
import AuctionChart from "./AuctionChart";
import { formatMonth } from "./format";
import type { AgricultureState } from "./useAgriculture";

type History =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; points: AgricultureHistoryPoint[] };

export const perKg = (price: string) => `K${price}/kg`;

export function periodLags(periodStart: string, now = new Date()): boolean {
  const lusaka = new Date(now.getTime() + 2 * 3600 * 1000);
  const current = `${lusaka.getUTCFullYear()}-${String(lusaka.getUTCMonth() + 1).padStart(2, "0")}`;
  return periodStart.slice(0, 7) < current;
}

const formatChartPrice = (value: number) => `K${value.toFixed(2)}`;

const AgricultureDetail = forwardRef<HTMLElement, {
  state: AgricultureState;
  focus: AgricultureCommodity | null;
  navigationKey: number;
  onRefresh: () => void;
}>(function AgricultureDetail({ state, focus, navigationKey, onRefresh }, ref) {
  const [commodity, setCommodity] = useState<AgricultureCommodity>(focus ?? "MAIZE");
  const [range, setRange] = useState<AgricultureRange>("ALL");
  const [history, setHistory] = useState<History>({ status: "loading" });

  useEffect(() => { if (focus) setCommodity(focus); }, [focus, navigationKey]);
  useEffect(() => {
    let cancelled = false;
    setHistory({ status: "loading" });
    getAgricultureHistory(commodity, range)
      .then((points) => !cancelled && setHistory({ status: "ready", points }))
      .catch((error: unknown) => !cancelled && setHistory({
        status: "error",
        message: error instanceof ApiError ? error.message : "History unavailable",
      }));
    return () => { cancelled = true; };
  }, [commodity, range, state]);

  const data = state.status === "ready" ? state.data : null;
  const byCommodity = new Map((data?.prices ?? []).map((p) => [p.commodity, p]));
  const selected = byCommodity.get(commodity);

  return (
    <article className="panel detail-panel" ref={ref} tabIndex={-1} aria-label="Zambia agriculture detail">
      <div className="panel-heading">
        <span>AGRICULTURE · NATIONAL AVERAGE PRICES</span>
        <button type="button" className="link-button" onClick={onRefresh} disabled={state.status === "loading"}>
          {state.status === "loading" ? "LOADING…" : "REFRESH"}
        </button>
      </div>
      <div className="detail-body">
        {state.status === "loading" && <p className="notice">Loading stored Ministry agriculture prices…</p>}
        {state.status === "error" && <p className="notice notice--error" role="alert">{state.message}. No prices are shown.</p>}
        {data && (
          <>
            <div className="table-scroll">
              <table>
                <thead><tr><th>COMMODITY</th><th>INDICATIVE AVG</th><th>PERIOD</th></tr></thead>
                <tbody>
                  {AGRICULTURE_COMMODITIES.map((item) => {
                    const row = byCommodity.get(item);
                    return (
                      <tr key={item} className={item === commodity ? "row--focus" : undefined}>
                        <td>{AGRICULTURE_LABELS[item]}</td>
                        <td>{row ? perKg(row.source_price) : "—"}</td>
                        <td>{row ? formatMonth(row.period_start) : "Not published"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {data.prices.length === 0 && (
              <p className="notice">
                No stored Ministry agriculture prices yet. Run <code>python -m app.jobs.backfill_agriculture --from 2025-04 --to 2026-01</code>.
              </p>
            )}
            {selected && (
              <>
                <p className="notice text-stale">
                  Latest supported Ministry period: {formatMonth(selected.period_start)}. This is a published indicative national average, not a live trade or exchange price.
                </p>
                <ul className="provenance">
                  <li><span>Source</span><a href={selected.source_url} target="_blank" rel="noreferrer">{selected.source}</a></li>
                  <li><span>Period</span>{formatMonth(selected.period_start)}</li>
                  <li><span>Basis</span>Indicative national average · ZMW/kg</li>
                  <li><span>Published</span>{selected.publication_date ? selected.publication_date : "Not stated"}</li>
                </ul>
              </>
            )}
          </>
        )}

        <section className="history-section" aria-label="Agriculture history">
          <div className="panel-heading"><span>{AGRICULTURE_LABELS[commodity]} · MONTHLY NATIONAL AVERAGE</span></div>
          <div className="range-row" role="group" aria-label="Agriculture commodity">
            {AGRICULTURE_COMMODITIES.map((item) => (
              <button key={item} type="button" className={item === commodity ? "range range--active" : "range"}
                aria-pressed={item === commodity} onClick={() => setCommodity(item)}>{AGRICULTURE_LABELS[item]}</button>
            ))}
          </div>
          <div className="range-row" role="group" aria-label="History range">
            {AGRICULTURE_RANGES.map((item) => (
              <button key={item} type="button" className={item === range ? "range range--active" : "range"}
                aria-pressed={item === range} onClick={() => setRange(item)}>{item}</button>
            ))}
          </div>
          {history.status === "loading" && <p className="notice">Loading stored agriculture history…</p>}
          {history.status === "error" && <p className="notice notice--error" role="alert">{history.message}. No history is shown.</p>}
          {history.status === "ready" && history.points.length === 0 && (
            <p className="notice">No stored {AGRICULTURE_LABELS[commodity]} observations in this range. Missing months are never filled.</p>
          )}
          {history.status === "ready" && history.points.length === 1 && (
            <p className="notice">Only one stored month: {perKg(history.points[0].source_price)} in {formatMonth(history.points[0].period_start)}.</p>
          )}
          {history.status === "ready" && history.points.length > 1 && (
            <>
              <AuctionChart
                points={history.points.map((p) => ({ auction_date: p.period_start, yield_rate: p.source_price }))}
                label={`${AGRICULTURE_LABELS[commodity]} Ministry national-average history, ZMW per kg`}
                precision={2} formatPrice={formatChartPrice} monthly
              />
              <p className="notice">
                {history.points.length} published monthly observations, {formatMonth(history.points[0].period_start)} to {formatMonth(history.points[history.points.length - 1].period_start)}. No interpolation or smoothing.
              </p>
            </>
          )}
        </section>
      </div>
    </article>
  );
});

export default AgricultureDetail;
