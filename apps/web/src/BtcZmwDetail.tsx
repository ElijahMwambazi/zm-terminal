import { forwardRef } from "react";
import { formatFixed, formatLusaka } from "./format";
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

const BtcZmwDetail = forwardRef<HTMLElement, { state: BtcZmwState; onRefresh: () => void }>(
  function BtcZmwDetail({ state, onRefresh }, ref) {
    const quote = state.status === "ready" ? state.quote : null;
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
        </div>
      </article>
    );
  },
);

export default BtcZmwDetail;
