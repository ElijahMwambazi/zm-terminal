import { forwardRef, useEffect, useState } from "react";
import { ApiError, BTC_RANGES, getBtcZmwHistory, type BtcRange, type BtcZmwHistory } from "./api";
import AuctionChart from "./AuctionChart";
import { extendsBeforeFxHistory } from "./btcRange";
import { formatDate, formatFixed, formatLusaka } from "./format";
import type { BtcZmwState } from "./useBtcZmw";

const usd = (value: string) => `$${formatFixed(value, 2, true)}`;
export const zmwPrice = (value: string) => `K${formatFixed(value, 2, true)}`;

/** Presentation only: seconds from the API as "30 s", "12 min", "22 h 43 min", "3 d 4 h". */
export function formatAge(seconds: number): string {
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} h ${minutes % 60} min`;
  return `${Math.floor(hours / 24)} d ${hours % 24} h`;
}

const chartZmw = (value: number) => `K${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const lusakaTime = (iso: string) =>
  new Intl.DateTimeFormat("en-ZM", { timeZone: "Africa/Lusaka", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(iso));
/** Charts do not draw a marker per point beyond this many points (the line and all data are unchanged). */
const MARKER_LIMIT = 250;
const TABLE_ROWS = 10;

type History =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: BtcZmwHistory; requestedAt: number };

const BtcZmwDetail = forwardRef<HTMLElement, { state: BtcZmwState; onRefresh: () => void }>(
  function BtcZmwDetail({ state, onRefresh }, ref) {
    const quote = state.status === "ready" ? state.quote : null;
    const [range, setRange] = useState<BtcRange>("1Y");
    const [history, setHistory] = useState<History>({ status: "loading" });
    // Stored-only: the history API never contacts Bitstamp or the Bank of Zambia.
    useEffect(() => {
      let cancelled = false;
      setHistory({ status: "loading" });
      const requestedAt = Date.now(); // the request time anchors the rolling-day window, as on the server
      getBtcZmwHistory(range)
        .then((data) => !cancelled && setHistory({ status: "ready", data, requestedAt }))
        .catch((error: unknown) =>
          !cancelled && setHistory({ status: "error", message: error instanceof ApiError ? error.message : "History unavailable" }));
      return () => { cancelled = true; };
    }, [range]);
    const points = history.status === "ready" ? history.data.observations : [];
    const recent = points.slice(-TABLE_ROWS).reverse();
    return (
      <article className="panel detail-panel" ref={ref} tabIndex={-1} aria-label="Indicative BTC/ZMW detail">
        <div className="panel-heading">
          <span>BITCOIN · INDICATIVE BTC/ZMW</span>
          <button type="button" className="link-button" onClick={onRefresh} disabled={state.status === "loading"}>
            {state.status === "loading" ? "LOADING…" : "REFRESH"}
          </button>
        </div>
        <div className="detail-body">
          <p className="notice notice--derived">
            <span className="badge-derived">DERIVED</span>{" "}
            {quote?.notice ?? "Indicative conversion, not an executable BTC/ZMW quote."}
          </p>
          {state.status === "loading" && <p className="notice">Requesting Bitstamp BTC/USD and reading the stored Bank of Zambia USD/ZMW…</p>}
          {state.status === "error" && <p className="notice notice--error" role="alert">{state.message}. No BTC/ZMW value is shown.</p>}
          {quote && (
            <>
              <div className="btc-equation" aria-label="BTC/ZMW calculation">
                <div className="btc-term"><span className="btc-label">BTC/USD</span><strong>{usd(quote.btc_usd_last)}</strong><small>Bitstamp last trade</small></div>
                <span className="btc-op" aria-hidden="true">×</span>
                <div className="btc-term"><span className="btc-label">USD/ZMW</span><strong>{formatFixed(quote.usd_zmw_mid, 4)}</strong><small>Bank of Zambia mid (stored)</small></div>
                <span className="btc-op" aria-hidden="true">=</span>
                <div className="btc-term btc-result"><span className="btc-label">BTC/ZMW</span><strong>{zmwPrice(quote.btc_zmw)}</strong><small>per bitcoin · shown rounded</small></div>
              </div>
              <div className="table-scroll">
                <table aria-label="Component timing">
                  <thead><tr><th>COMPONENT</th><th>SOURCE</th><th>OBSERVED</th><th>AGE</th><th>STATUS</th></tr></thead>
                  <tbody>
                    <tr>
                      <td>BTC/USD last</td><td>Bitstamp</td><td>{formatLusaka(quote.btc_observed_at)}</td><td>{formatAge(quote.btc_age_seconds)}</td>
                      <td>{quote.btc_stale ? <span className="text-stale">Stale</span> : "Current"}</td>
                    </tr>
                    <tr>
                      <td>USD/ZMW mid</td><td>Bank of Zambia</td><td>{formatLusaka(quote.fx_observed_at)}</td><td>{formatAge(quote.fx_age_seconds)}</td>
                      <td>{quote.fx_stale ? <span className="text-stale">Stale</span> : "Not stale"}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <p className={quote.derived_stale ? "notice text-stale" : "notice"}>
                {quote.derived_stale ? "At least one input is stale. " : ""}
                Bitcoin trades continuously but the Bank of Zambia publishes only at business-day times, so the USD/ZMW input is normally older than the Bitcoin input (here {formatAge(quote.fx_age_seconds)} against {formatAge(quote.btc_age_seconds)}). The Bank of Zambia rate is read from storage, never fetched by this view; refresh USD/ZMW to store a newer one. Calculated {formatLusaka(quote.derived_at)}.
              </p>
              <ul className="provenance">
                <li><span>BTC/USD</span><a href={quote.btc_source_url} target="_blank" rel="noreferrer">Bitstamp public API</a> · {quote.btc_market} SPOT · {quote.btc_price_type.replace("_", " ")}</li>
                <li><span>USD/ZMW</span><a href={quote.fx_source_url} target="_blank" rel="noreferrer">Bank of Zambia exchange rates</a> · {quote.fx_price} (stale after 96 hours)</li>
                <li><span>Method</span>{quote.methodology}</li>
                <li><span>Not</span>An exchange-traded BTC/ZMW price, a Zambian dealer, P2P or mobile-money rate, or an executable quote.</li>
              </ul>
            </>
          )}
          <section className="history-section" aria-label="Derived daily BTC/ZMW history">
            <div className="panel-heading"><span>BTC/ZMW · DERIVED DAILY HISTORY (STORED INPUTS)</span><span className="badge-derived">DERIVED</span></div>
            <p className="notice">
              Derived from stored Bitstamp BTC/USD daily closes and stored Bank of Zambia USD/ZMW observations. Dates without BoZ observations are omitted; no interpolation or forward-fill. The BTC close and FX observations are not simultaneous: the close is the last trade of a completed UTC day, and the FX value is the mean of that Zambia date's BoZ observations. They represent different observation times within the matched calendar date.
            </p>
            <div className="range-row" role="group" aria-label="History range">
              {BTC_RANGES.map((item) => (
                <button key={item} type="button" className={item === range ? "range range--active" : "range"}
                  aria-pressed={item === range} onClick={() => setRange(item)}>{item}</button>
              ))}
            </div>
            {history.status === "loading" && <p className="notice">Loading stored inputs…</p>}
            {history.status === "error" && <p className="notice notice--error" role="alert">{history.message}. No history is shown.</p>}
            {history.status === "ready" && (
              <>
                {history.data.count === 0 ? (
                  <p className="notice">
                    No derived days in this range: no stored BTC/USD daily candle has a stored Bank of Zambia USD/ZMW observation on its date.
                    {history.data.fx_stored_from ? ` Stored BoZ USD/ZMW history begins ${formatDate(history.data.fx_stored_from)}.` : " No BoZ USD/ZMW observations are stored yet."}
                  </p>
                ) : (
                  <>
                    {history.data.count === 1 ? (
                      <p className="notice">Only one derived day in this range: {zmwPrice(points[0].btc_zmw)} on {formatDate(points[0].date)}.</p>
                    ) : (
                      <AuctionChart
                        points={points.map((p) => ({ auction_date: p.date, yield_rate: p.btc_zmw }))}
                        label="Derived daily BTC/ZMW history, kwacha per bitcoin"
                        precision={2} formatPrice={chartZmw} markers={points.length <= MARKER_LIMIT} />
                    )}
                    <p className="notice">
                      {history.data.count} derived {history.data.count === 1 ? "day" : "days"}, {formatDate(points[0].date)} to {formatDate(points[points.length - 1].date)}.
                      {" "}{history.data.btc_days_without_fx} stored BTC/USD {history.data.btc_days_without_fx === 1 ? "day" : "days"} in this range had no BoZ observation and {history.data.btc_days_without_fx === 1 ? "is" : "are"} omitted
                      (normally weekends, holidays and any dates before the stored BoZ history; there is no weekday exclusion rule, so a date with a stored BoZ observation is derived). The chart joins real points with a line; it does not imply daily observations. This is not the live quote above: that uses the current Bitstamp last trade.
                    </p>
                    <div className="table-scroll">
                      <table aria-label="Recent derived days">
                        <thead><tr><th>DATE</th><th>BTC/ZMW</th><th>BTC/USD CLOSE (UTC DAY)</th><th>BOZ DAILY MEAN</th><th>BOZ OBS</th><th>FIRST–LAST OBS (CAT)</th></tr></thead>
                        <tbody>
                          {recent.map((p) => (
                            <tr key={p.date}>
                              <td>{formatDate(p.date)}</td><td>{zmwPrice(p.btc_zmw)}</td><td>{usd(p.btc_usd_close)}</td>
                              <td>{formatFixed(p.usd_zmw_daily_mid_avg, 4)}</td><td>{p.fx_observation_count}</td>
                              <td>{lusakaTime(p.fx_first_observation)}–{lusakaTime(p.fx_last_observation)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </>
                )}
                {/* A source boundary, decided from the requested window and the global fx_stored_from, never from the derived points. */}
                {history.data.fx_stored_from && extendsBeforeFxHistory(history.data.range, history.data.fx_stored_from, history.requestedAt) && (
                  <p className="notice">
                    BTC/USD history extends further back, but derived BTC/ZMW is available only from the stored Bank of Zambia source window (it begins {formatDate(history.data.fx_stored_from)}); this is a source boundary, not missing Bitcoin data.
                  </p>
                )}
              </>
            )}
          </section>
        </div>
      </article>
    );
  },
);

export default BtcZmwDetail;
