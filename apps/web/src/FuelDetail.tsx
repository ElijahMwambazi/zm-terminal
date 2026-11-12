import { forwardRef, useEffect, useState } from "react";
import {
  ApiError, FUEL_LABELS, FUEL_PRODUCTS, FUEL_RANGES, getFuelHistory,
  type FuelHistoryPoint, type FuelPrice, type FuelProduct, type FuelRange,
} from "./api";
import AuctionChart from "./AuctionChart";
import FuelBuildUpView from "./FuelBuildUpView";
import { formatLusaka, formatMonth } from "./format";
import type { FuelState } from "./useFuel";

export const perLitre = (price: string) => `K${price}/L`;

/** True if ERB's latest stored month is before the current Africa/Lusaka month. */
export function archiveLags(effectiveDate: string, now = new Date()): boolean {
  const lusaka = new Date(now.getTime() + 2 * 3600 * 1000); // UTC+2 year-round
  const current = `${lusaka.getUTCFullYear()}-${String(lusaka.getUTCMonth() + 1).padStart(2, "0")}`;
  return effectiveDate.slice(0, 7) < current;
}

type History =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; points: FuelHistoryPoint[] };

const formatChartPrice = (value: number) => `K${value.toFixed(2)}`;

const FuelDetail = forwardRef<HTMLElement, { state: FuelState; focus: FuelProduct | null; onRefresh: () => void }>(
  function FuelDetail({ state, focus, onRefresh }, ref) {
    const [product, setProduct] = useState<FuelProduct>(focus ?? "PETROL");
    const [range, setRange] = useState<FuelRange>("1Y");
    const [view, setView] = useState<"history" | "pump" | "wholesale">("history");
    const [history, setHistory] = useState<History>({ status: "loading" });
    useEffect(() => { if (focus) setProduct(focus); }, [focus]);
    useEffect(() => {
      let cancelled = false;
      setHistory({ status: "loading" });
      getFuelHistory(product, range)
        .then((points) => !cancelled && setHistory({ status: "ready", points }))
        .catch((error: unknown) =>
          !cancelled && setHistory({ status: "error", message: error instanceof ApiError ? error.message : "History unavailable" }));
      return () => { cancelled = true; };
    }, [product, range, state]);

    const data = state.status === "ready" ? state.data : null;
    const byProduct = new Map<FuelProduct, FuelPrice>((data?.prices ?? []).map((p) => [p.product, p]));
    const first = data?.prices[0];
    const lagging = data?.prices.some((p) => archiveLags(p.effective_date)) ?? false;

    return (
      <article className="panel detail-panel" ref={ref} tabIndex={-1} aria-label="Zambia fuel detail">
        <div className="panel-heading">
          <span>ZAMBIA FUEL · ERB RETAIL PUMP PRICES</span>
          <button type="button" className="link-button" onClick={onRefresh} disabled={state.status === "loading"}>
            {state.status === "loading" ? "LOADING…" : "REFRESH"}
          </button>
        </div>
        <div className="detail-body">
          {state.status === "loading" && <p className="notice">Loading stored ERB prices…</p>}
          {state.status === "error" && <p className="notice notice--error" role="alert">{state.message}. No prices are shown.</p>}
          {data && (
            <>
              <div className="table-scroll">
                <table>
                  <thead><tr><th>PRODUCT</th><th>PRICE</th><th>MONTH</th></tr></thead>
                  <tbody>
                    {FUEL_PRODUCTS.map((p) => {
                      const row = byProduct.get(p);
                      return (
                        <tr key={p} className={focus === p ? "row--focus" : undefined}>
                          <td>{FUEL_LABELS[p]}</td>
                          <td>{row ? perLitre(row.price_zmw) : "—"}</td>
                          <td>{row ? formatMonth(row.effective_date) : "Not published"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {data.prices.length === 0 && (
                <p className="notice">No stored ERB prices yet. Import the official archive with <code>python -m app.jobs.backfill_fuel --from 2012-01-01</code>.</p>
              )}
              {focus && data.prices.length > 0 && !byProduct.has(focus) && (
                <p className="notice notice--error">ERB has not published a retail {FUEL_LABELS[focus]} price. No value is shown.</p>
              )}
              {lagging && (
                <p className="notice text-stale">
                  The newest month in ERB&apos;s structured archive is shown. Later monthly prices may already be announced in ERB press releases (PDF) that this view does not read.
                </p>
              )}
              {first && (
                <ul className="provenance">
                  <li><span>Source</span><a href={first.source_url} target="_blank" rel="noreferrer">Energy Regulation Board Zambia</a> (official pump and wholesale prices archive)</li>
                  <li><span>Retrieved</span>{formatLusaka(first.retrieved_at)}</li>
                  <li><span>Note</span>ZMW per litre, one price per pricing month. A month ERB listed twice with different prices is omitted, not guessed.</li>
                </ul>
              )}
            </>
          )}
          <section className="history-section" aria-label="Fuel product detail">
            <div className="panel-heading"><span>{FUEL_LABELS[product]} · DETAIL</span></div>
            <div className="range-row" role="group" aria-label="Fuel product">
              {FUEL_PRODUCTS.map((item) => (
                <button key={item} type="button" className={item === product ? "range range--active" : "range"}
                  aria-pressed={item === product} onClick={() => setProduct(item)}>{FUEL_LABELS[item]}</button>
              ))}
            </div>
            <div className="range-row" role="group" aria-label="Fuel view">
              {([["history", "RETAIL HISTORY"], ["pump", "PUMP BUILD-UP"], ["wholesale", "WHOLESALE BUILD-UP"]] as const).map(([key, label]) => (
                <button key={key} type="button" className={key === view ? "range range--active" : "range"}
                  aria-pressed={key === view} onClick={() => setView(key)}>{label}</button>
              ))}
            </div>
            {view !== "history" && (
              <FuelBuildUpView product={product} type={view} retail={byProduct.get(product)} reloadKey={state} />
            )}
          </section>
          <section className="history-section" aria-label="Fuel history" hidden={view !== "history"}>
            <div className="panel-heading"><span>{FUEL_LABELS[product]} · RETAIL PRICE BY MONTH</span></div>
            <div className="range-row" role="group" aria-label="History range">
              {FUEL_RANGES.map((item) => (
                <button key={item} type="button" className={item === range ? "range range--active" : "range"}
                  aria-pressed={item === range} onClick={() => setRange(item)}>{item}</button>
              ))}
            </div>
            {history.status === "loading" && <p className="notice">Loading stored history…</p>}
            {history.status === "error" && <p className="notice notice--error" role="alert">{history.message}. No history is shown.</p>}
            {history.status === "ready" && history.points.length === 0 && (
              <p className="notice">No stored {FUEL_LABELS[product]} prices in this range. Months ERB did not publish are never filled in.</p>
            )}
            {history.status === "ready" && history.points.length === 1 && (
              <p className="notice">Only one stored month in this range: {perLitre(history.points[0].price_zmw)} in {formatMonth(history.points[0].effective_date)}.</p>
            )}
            {history.status === "ready" && history.points.length > 1 && (
              <>
                <AuctionChart
                  points={history.points.map((p) => ({ auction_date: p.effective_date, yield_rate: p.price_zmw }))}
                  label={`${FUEL_LABELS[product]} retail price history, ZMW per litre`}
                  precision={2} formatPrice={formatChartPrice} monthly />
                <p className="notice">{history.points.length} stored months, {formatMonth(history.points[0].effective_date)} to {formatMonth(history.points[history.points.length - 1].effective_date)}. ZMW per litre · Source: ERB.</p>
              </>
            )}
          </section>
        </div>
      </article>
    );
  },
);

export default FuelDetail;
