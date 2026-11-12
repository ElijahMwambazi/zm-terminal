import { forwardRef } from "react";
import { TENORS, type Tenor, type TreasuryBill } from "./api";
import { formatDate, formatLusaka } from "./format";
import type { TbillState } from "./useTreasuryBills";

export function tbillProblem(state: TbillState): string | null {
  if (state.status !== "error") return null;
  return state.kind === "upstream"
    ? "Bank of Zambia data is currently unavailable. No rates are shown."
    : "ZM Terminal API is unavailable. No rates are shown.";
}

/** Strong claim: only when a rejected tender newer than this row's auction exists. */
export const fallbackWarning = (row: TreasuryBill): string | null =>
  row.newer_rejected_tenders.length > 0
    ? `LATEST TENDER REJECTED (${row.newer_rejected_tenders.join(", ")}) · USING EARLIER VALID AUCTION`
    : null;

/** Neutral reporting of rejected source tenders that do not affect the value shown. */
export const rejectedNote = (skipped: string[]): string | null =>
  skipped.length > 0 ? `Rejected source tender: ${skipped.join(", ")}` : null;

const pct = (value: string) => `${value}%`;

const TbillDetail = forwardRef<HTMLElement, { state: TbillState; focus: Tenor | null; onRefresh: () => void }>(
  function TbillDetail({ state, focus, onRefresh }, ref) {
    const problem = tbillProblem(state);
    const data = state.status === "ready" ? state.data : null;
    const byTenor = new Map<Tenor, TreasuryBill>((data?.tenors ?? []).map((row) => [row.tenor, row]));
    const focused = focus ? byTenor.get(focus) : undefined;
    const retrieved = data?.tenors[0]?.retrieved_at;

    return (
      <article className="panel detail-panel" ref={ref} tabIndex={-1} aria-label="Treasury Bills detail">
        <div className="panel-heading">
          <span>ZAMBIA T-BILLS · BANK OF ZAMBIA AUCTIONS</span>
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
                  <tr><th>TENOR</th><th>YIELD</th><th>DISCOUNT</th><th>AUCTION</th><th>ISIN</th></tr>
                </thead>
                <tbody>
                  {TENORS.map((tenor) => {
                    const row = byTenor.get(tenor);
                    const cls = focus === tenor ? "row--focus" : undefined;
                    return row ? (
                      <tr key={tenor} className={cls}>
                        <td>{tenor}</td>
                        <td>{pct(row.yield_rate)}</td>
                        <td>{pct(row.discount_rate)}</td>
                        <td>
                          {formatDate(row.auction_date)} · {row.tender_number}
                          {row.stale && <span className="text-stale"> · STALE</span>}
                          {row.newer_rejected_tenders.length > 0 && <span className="text-stale"> · FALLBACK</span>}
                        </td>
                        <td>{row.isin}</td>
                      </tr>
                    ) : (
                      <tr key={tenor} className={cls}>
                        <td>{tenor}</td><td>—</td><td>—</td><td>No recent auction published</td><td>—</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              </div>
              {focus && !focused && (
                <p className="notice notice--error">No recent {focus} auction is published. No rate is shown.</p>
              )}
              {data.tenors.some((row) => fallbackWarning(row)) && (
                <p className="notice text-stale">
                  {[...new Set(data.tenors.filter((row) => fallbackWarning(row)).flatMap((row) => row.newer_rejected_tenders))]
                    .map((n) => `Tender ${n}`).join(", ")} failed validation and was ignored. Rows marked FALLBACK come from an earlier valid auction.
                </p>
              )}
              {!data.tenors.some((row) => fallbackWarning(row)) && rejectedNote(data.skipped_tenders) && (
                <p className="notice">{rejectedNote(data.skipped_tenders)} (ignored; it does not affect the rows shown).</p>
              )}
              <ul className="provenance">
                <li><span>Source</span><a href={data.tenors[0]?.source_url} target="_blank" rel="noreferrer">Bank of Zambia</a> (official auction results)</li>
                {retrieved && <li><span>Retrieved</span>{formatLusaka(retrieved)}</li>}
                <li><span>Note</span>Yield and discount are percent per annum as published. Each row keeps its own auction date; tenors are not always auctioned together.</li>
              </ul>
            </>
          )}
        </div>
      </article>
    );
  },
);

export default TbillDetail;
