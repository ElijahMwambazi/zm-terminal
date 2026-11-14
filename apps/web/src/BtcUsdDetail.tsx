import { forwardRef, useEffect, useState } from "react";
import { ApiError, BTC_RANGES, getBtcUsdHistory, type BtcDailyCandle, type BtcRange } from "./api";
import AuctionChart from "./AuctionChart";
import { formatDate, formatFixed, formatLusaka } from "./format";
import type { BtcUsdState } from "./useBtcUsd";

export const usdPrice = (value: string) => `$${formatFixed(value, 2, true)}`;
const btcVolume = (value: string) => `${formatFixed(value, 2, true)} BTC`;
const chartUsd = (value: number) => `$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const candleDate = (candle: BtcDailyCandle) => candle.candle_open_at.slice(0, 10); // a UTC calendar day
/** Charts do not draw a marker per candle beyond this many points (the line and all data are unchanged). */
const MARKER_LIMIT = 250;
const TABLE_ROWS = 10;

type History =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; candles: BtcDailyCandle[] };

const BtcUsdDetail = forwardRef<HTMLElement, { state: BtcUsdState; onRefresh: () => void }>(
  function BtcUsdDetail({ state, onRefresh }, ref) {
    const [range, setRange] = useState<BtcRange>("1Y");
    const [history, setHistory] = useState<History>({ status: "loading" });
    useEffect(() => {
      let cancelled = false;
      setHistory({ status: "loading" });
      getBtcUsdHistory(range)
        .then((candles) => !cancelled && setHistory({ status: "ready", candles }))
        .catch((error: unknown) =>
          !cancelled && setHistory({ status: "error", message: error instanceof ApiError ? error.message : "History unavailable" }));
      return () => { cancelled = true; };
    }, [range]);

    const quote = state.status === "ready" ? state.quote : null;
    const candles = history.status === "ready" ? history.candles : [];
    const latest = candles[candles.length - 1];
    const recent = candles.slice(-TABLE_ROWS).reverse();

    return (
      <article className="panel detail-panel" ref={ref} tabIndex={-1} aria-label="BTC/USD detail">
        <div className="panel-heading">
          <span>BITCOIN · BTC/USD <span className="muted">BITSTAMP SPOT</span></span>
          <button type="button" className="link-button" onClick={onRefresh} disabled={state.status === "loading"}>
            {state.status === "loading" ? "LOADING…" : "REFRESH"}
          </button>
        </div>
        <div className="detail-body">
          <p className="notice">Bitstamp BTC/USD spot: the latest executed trade on one exchange, in US dollars. It is not a global Bitcoin reference price and not converted to ZMW here. Local/internal use: public display and redistribution rights are pending.</p>
          {state.status === "loading" && <p className="notice">Requesting the Bitstamp BTC/USD ticker…</p>}
          {state.status === "error" && <p className="notice notice--error" role="alert">{state.message}. No live price is shown.</p>}
          {quote && (
            <>
              <dl className="quote-grid">
                <div><dt>LAST TRADE</dt><dd className="quote-mid">{usdPrice(quote.last)}</dd></div>
                <div><dt>BID</dt><dd>{usdPrice(quote.bid)}</dd></div>
                <div><dt>ASK</dt><dd>{usdPrice(quote.ask)}</dd></div>
              </dl>
              <dl className="quote-grid btc-24h">
                <div><dt>24H HIGH</dt><dd>{usdPrice(quote.high_24h)}</dd></div>
                <div><dt>24H LOW</dt><dd>{usdPrice(quote.low_24h)}</dd></div>
                <div><dt>24H VWAP</dt><dd>{usdPrice(quote.vwap_24h)}</dd></div>
                <div><dt>24H VOLUME</dt><dd>{btcVolume(quote.volume_24h_btc)}</dd></div>
              </dl>
              <p className="notice">High, low, VWAP and volume are Bitstamp's rolling last-24-hours values, not UTC-day statistics.</p>
              <ul className="provenance">
                <li><span>Source</span><a href={quote.source_url} target="_blank" rel="noreferrer">Bitstamp public API</a> · {quote.market} {quote.market_type} · last trade</li>
                <li><span>Observed</span>{formatLusaka(quote.observed_at)} (Bitstamp ticker time)</li>
                <li><span>Retrieved</span>{formatLusaka(quote.retrieved_at)}</li>
                <li><span>Freshness</span>{quote.stale
                  ? <span className="text-stale">Older than {Math.round(quote.stale_after_seconds / 60)} minutes (stale)</span>
                  : `Within ${Math.round(quote.stale_after_seconds / 60)} minutes (application heuristic, not a Bitstamp guarantee)`}</li>
              </ul>
            </>
          )}
          <section className="history-section" aria-label="BTC/USD daily history">
            <div className="panel-heading"><span>BTC/USD · DAILY CLOSE (STORED)</span></div>
            <div className="range-row" role="group" aria-label="History range">
              {BTC_RANGES.map((item) => (
                <button key={item} type="button" className={item === range ? "range range--active" : "range"}
                  aria-pressed={item === range} onClick={() => setRange(item)}>{item}</button>
              ))}
            </div>
            {history.status === "loading" && <p className="notice">Loading stored daily candles…</p>}
            {history.status === "error" && <p className="notice notice--error" role="alert">{history.message}. No history is shown.</p>}
            {history.status === "ready" && candles.length === 0 && (
              <p className="notice">No stored BTC/USD daily candles in this range. Import them with <code>python -m app.jobs.backfill_btc</code>.</p>
            )}
            {history.status === "ready" && candles.length === 1 && (
              <p className="notice">Only one stored daily candle in this range: close {usdPrice(candles[0].close)} on {formatDate(candleDate(candles[0]))}.</p>
            )}
            {history.status === "ready" && candles.length > 1 && (
              <>
                <AuctionChart
                  points={candles.map((c) => ({ auction_date: candleDate(c), yield_rate: c.close }))}
                  label="BTC/USD daily close history, US dollars"
                  precision={2} formatPrice={chartUsd} markers={candles.length <= MARKER_LIMIT} />
                <p className="notice">
                  {candles.length} stored daily candles, {formatDate(candleDate(candles[0]))} to {formatDate(candleDate(latest))}. Each point is the close (last trade) of a completed UTC day, not an average. Days Bitstamp did not publish are never filled in.
                </p>
              </>
            )}
            {latest && (
              <>
                <p className="notice">Latest completed daily candle: {formatDate(candleDate(latest))} (UTC day).</p>
                <div className="table-scroll">
                  <table aria-label="Recent daily candles">
                    <thead><tr><th>DAY (UTC)</th><th>OPEN</th><th>HIGH</th><th>LOW</th><th>CLOSE</th><th>VOLUME</th></tr></thead>
                    <tbody>
                      {recent.map((c) => (
                        <tr key={c.candle_open_at}>
                          <td>{formatDate(candleDate(c))}</td><td>{usdPrice(c.open)}</td><td>{usdPrice(c.high)}</td>
                          <td>{usdPrice(c.low)}</td><td>{usdPrice(c.close)}</td><td>{btcVolume(c.volume_btc)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </section>
        </div>
      </article>
    );
  },
);

export default BtcUsdDetail;
