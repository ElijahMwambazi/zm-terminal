import { forwardRef, useEffect } from "react";
import type { CurvePoint } from "./api";
import { formatDate, formatFixed } from "./format";
import type { YieldCurveState } from "./useYieldCurve";
import YieldCurveChart from "./YieldCurveChart";

const lusakaToday = () => new Date(Date.now() + 2 * 3600 * 1000).toISOString().slice(0, 10); // Africa/Lusaka is UTC+2 year-round; input max only
const instrument = (p: CurvePoint) => (p.instrument_type === "treasury_bill" ? "T-bill" : "Gov bond");
const supporting = (p: CurvePoint) =>
  p.instrument_type === "treasury_bill" ? `discount ${formatFixed(p.discount_rate, 4)}%` : `coupon ${formatFixed(p.coupon_rate, 4)}%`;

type Props = {
  state: YieldCurveState;
  asOf: string | null;
  onAsOf: (value: string | null) => void;
  navigationKey: number;
  onRefresh: () => void;
};

const YieldCurveDetail = forwardRef<HTMLElement, Props>(function YieldCurveDetail({ state, asOf, onAsOf, navigationKey, onRefresh }, ref) {
  // Every CURVE ZM command returns to the latest snapshot, even when the view is already open on a historical date.
  useEffect(() => { onAsOf(null); }, [navigationKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const curve = state.status === "ready" ? state.data : null;

  return (
    <article className="panel detail-panel" ref={ref} tabIndex={-1} aria-label="Zambia sovereign auction yield curve">
      <div className="panel-heading">
        <span>ZAMBIA SOVEREIGN · AUCTION YIELD CURVE</span>
        <button type="button" className="link-button" onClick={onRefresh} disabled={state.status === "loading"}>
          {state.status === "loading" ? "LOADING…" : "REFRESH"}
        </button>
      </div>
      <div className="detail-body">
        <p className="notice notice--derived">
          <span className="badge-derived">DERIVED</span>{" "}
          {curve?.notice ?? "Derived from BoZ auction results. Points may come from different auction dates. Bond tenors are published tenor buckets, not calculated remaining maturities."}
        </p>
        <div className="curve-controls">
          <label htmlFor="curve-as-of">AS OF</label>
          <input id="curve-as-of" type="date" value={asOf ?? ""} max={lusakaToday()} onChange={(event) => onAsOf(event.target.value || null)} />
          <button type="button" className="range" onClick={() => onAsOf(null)} disabled={asOf === null} aria-pressed={asOf === null}>LATEST</button>
          <span className="muted">{asOf === null ? "Latest stored snapshot (Africa/Lusaka today)" : "Historical stored snapshot"}</span>
        </div>
        {state.status === "loading" && <p className="notice">Loading stored BoZ auction results…</p>}
        {state.status === "error" && <p className="notice notice--error" role="alert">{state.message}. No curve is shown.</p>}
        {state.status === "unavailable" && (
          <p className="notice" role="status">No stored accepted BoZ auction observation exists on or before {asOf ? formatDate(asOf) : "today"}, so no curve can be shown.</p>
        )}
        {curve && (
          <>
            <p className="curve-vintage">
              <strong>As of {formatDate(curve.as_of)}</strong>
              {" · "}
              {curve.mixed_vintage
                ? <span className="text-stale">Mixed auction vintages</span>
                : <span>Single auction date</span>}
              {" · "}
              {curve.mixed_vintage
                ? <>Auction dates {formatDate(curve.oldest_point_date)} to {formatDate(curve.newest_point_date)} · {curve.vintage_span_days}-day span</>
                : <>{formatDate(curve.newest_point_date)}</>}
              {" · "}{curve.point_count} of {curve.point_count + curve.missing_tenors.length} tenors
            </p>
            <p className="notice">Each tenor uses its latest stored accepted BoZ auction observation at or before the as-of date. The as-of date is a cutoff, not a common observation date.</p>
            <YieldCurveChart points={curve.points} missing={curve.missing_tenors} />
            <p className="curve-legend">
              <span className="legend-mark legend-bill" /> T-bill auction yield · <span className="legend-mark legend-bond" /> bond auction yield · dashed line: visual connector only, no interpolated yields
            </p>
            {curve.missing_tenors.length > 0 && (
              <p className="notice text-stale">No stored accepted observation on or before this date for: {curve.missing_tenors.join(", ")}. They are left empty, never interpolated or substituted.</p>
            )}
            <div className="table-scroll">
              <table aria-label="Curve points">
                <thead>
                  <tr><th>TENOR</th><th>YIELD %</th><th>INSTRUMENT</th><th>AUCTION DATE</th><th>AGE</th><th>TENDER</th><th>ISIN</th><th>SUPPORTING</th></tr>
                </thead>
                <tbody>
                  {curve.points.map((p) => (
                    <tr key={p.tenor}>
                      <td>{p.tenor}</td><td>{formatFixed(p.yield_rate, 4)}</td><td>{instrument(p)}</td><td>{formatDate(p.auction_date)}</td>
                      <td>{p.age_days}d</td><td>{p.tender_number}</td><td>{p.isin}</td><td className="muted">{supporting(p)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <ul className="provenance">
              <li><span>Method</span>{curve.methodology}</li>
              <li><span>Yields</span>Cut-off/published BoZ auction yields as stored (percent per annum, not divided by 100); the T-bill discount rate and the bond coupon are supporting columns only.</li>
              <li><span>Tenors</span>T-bill and bond tenors are BoZ's published buckets, combined as research context. This is not a bootstrapped, fitted or spot curve and not an official BoZ curve. BoZ reopens existing bonds, so a bond tenor label is not an exact remaining maturity; no maturity dates are stored or derived.</li>
              <li><span>Stored</span>Accepted observations only: a newer BoZ tender that failed validation is not stored here, so a point is the latest stored accepted observation, not necessarily the latest BoZ auction. AGE is days since the auction date.</li>
              <li><span>Source</span>Bank of Zambia auction results ({curve.points.some((p) => p.instrument_type === "treasury_bill") ? "treasury bills" : ""}{curve.points.some((p) => p.instrument_type === "treasury_bill") && curve.points.some((p) => p.instrument_type === "government_bond") ? ", " : ""}{curve.points.some((p) => p.instrument_type === "government_bond") ? "government bonds" : ""}); reuse terms are not yet reviewed.</li>
            </ul>
          </>
        )}
      </div>
    </article>
  );
});

export default YieldCurveDetail;
