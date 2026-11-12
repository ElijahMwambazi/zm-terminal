import { forwardRef } from "react";
import { BOND_TENORS, type BondTenor, type GovernmentBond } from "./api";
import { formatDate, formatLusaka } from "./format";
import type { BondState } from "./useGovernmentBonds";

export function bondProblem(state: BondState): string | null {
  if (state.status !== "error") return null;
  return state.kind === "upstream"
    ? "Bank of Zambia data is currently unavailable. No yields are shown."
    : "ZM Terminal API is unavailable. No yields are shown.";
}

/** Strong claim only for a row whose own tenor had a newer rejected record (or a tender-level rejection). */
export const bondFallbackWarning = (row: GovernmentBond): string | null =>
  row.newer_rejected_tenders.length > 0
    ? `FALLBACK — NEWER ${row.tenor} DATA REJECTED (${row.newer_rejected_tenders.join(", ")}) · USING EARLIER VALID AUCTION`
    : null;

const pct = (value: string) => `${value}%`;

const BondDetail = forwardRef<HTMLElement, { state: BondState; focus: BondTenor | null; onRefresh: () => void }>(
  function BondDetail({ state, focus, onRefresh }, ref) {
    const problem = bondProblem(state);
    const data = state.status === "ready" ? state.data : null;
    const byTenor = new Map<BondTenor, GovernmentBond>((data?.tenors ?? []).map((row) => [row.tenor, row]));
    const focused = focus ? byTenor.get(focus) : undefined;
    const anyFallback = data?.tenors.some((row) => bondFallbackWarning(row)) ?? false;
    const retrieved = data?.tenors[0]?.retrieved_at;

    return (
      <article className="panel detail-panel" ref={ref} tabIndex={-1} aria-label="Government Bonds detail">
        <div className="panel-heading">
          <span>ZAMBIA GOVERNMENT BONDS · BANK OF ZAMBIA AUCTIONS</span>
          <button type="button" className="link-button" onClick={onRefresh} disabled={state.status === "loading"}>
            {state.status === "loading" ? "LOADING…" : "REFRESH"}
          </button>
        </div>
        <div className="detail-body">
          {state.status === "loading" && <p className="notice">Loading Bank of Zambia auction results…</p>}
          {problem && <p className="notice notice--error" role="alert">{problem}</p>}
          {data && (
            <>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr><th>TENOR</th><th>YIELD</th><th>COUPON</th><th>AUCTION</th><th>ISIN</th></tr>
                  </thead>
                  <tbody>
                    {BOND_TENORS.map((tenor) => {
                      const row = byTenor.get(tenor);
                      const cls = focus === tenor ? "row--focus" : undefined;
                      return row ? (
                        <tr key={tenor} className={cls}>
                          <td>{tenor}</td>
                          <td>{pct(row.yield_rate)}</td>
                          <td>{pct(row.coupon_rate)}</td>
                          <td>
                            {formatDate(row.auction_date)} · {row.tender_number}
                            {bondFallbackWarning(row) && <span className="text-stale"> · FALLBACK</span>}
                          </td>
                          <td>{row.isin}</td>
                        </tr>
                      ) : (
                        <tr key={tenor} className={cls}>
                          <td>{tenor}</td><td>—</td><td>—</td>
                          <td>No valid auction found{data.unverified_tenors.includes(tenor) && " (rejected source record)"}</td><td>—</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {focus && !focused && (
                <p className="notice notice--error">
                  No valid {focus} auction was found in BoZ&apos;s auction archive. No yield is shown.
                </p>
              )}
              {anyFallback && (
                <p className="notice text-stale">
                  Rejected source data in {[...new Set(data.tenors.flatMap((row) => row.newer_rejected_tenders))].join(", ")} was
                  ignored. Only rows marked FALLBACK could have been affected; they come from an earlier valid auction.
                </p>
              )}
              {!anyFallback && data.skipped_tenders.length > 0 && (
                <p className="notice">
                  Rejected source records in {data.skipped_tenders.join(", ")} (ignored).{" "}
                  {data.unverified_tenors.length > 0
                    ? `${data.unverified_tenors.join(", ")} may appear only in rejected records; no value is shown for it.`
                    : "It does not affect the rows shown."}
                </p>
              )}
              <ul className="provenance">
                <li><span>Source</span><a href={data.tenors[0]?.source_url} target="_blank" rel="noreferrer">Bank of Zambia</a> (official auction results)</li>
                {retrieved && <li><span>Retrieved</span>{formatLusaka(retrieved)}</li>}
                <li><span>Note</span>Yield and coupon are percent per annum as published. Not every tenor is auctioned each time, so a row can come from an older auction, and a reopened bond keeps its ISIN and coupon, so each row shows its own auction date. No freshness flag: BoZ publishes no machine-readable schedule.</li>
              </ul>
            </>
          )}
        </div>
      </article>
    );
  },
);

export default BondDetail;
