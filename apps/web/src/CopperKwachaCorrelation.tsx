import { useEffect, useState } from "react";
import { ApiError, CORRELATION_WINDOWS, getCopperKwachaCorrelation, type CopperKwachaCorrelation, type CopperRange, type CorrelationWindow } from "./api";
import AuctionChart from "./AuctionChart";
import { formatMonth } from "./format";

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: CopperKwachaCorrelation };

const r3 = (value: string) => Number(value).toFixed(3);
const SMALL_SAMPLE = "Small sample: this coefficient is based on fewer than 12 monthly changes and may be unstable.";

/** Correlation section of the COPPER FX view. The coefficient is calculated by the API; the browser only displays it. */
export default function CopperKwachaCorrelationSection({ range, reloadKey }: { range: CopperRange; reloadKey: number }) {
  const [window, setWindow] = useState<CorrelationWindow>("6M");
  const [state, setState] = useState<State>({ status: "loading" });
  useEffect(() => {
    let cancelled = false; // a late response for an old range/window must not overwrite the current one
    setState({ status: "loading" });
    getCopperKwachaCorrelation(range, window)
      .then((data) => !cancelled && setState({ status: "ready", data }))
      .catch((error: unknown) =>
        !cancelled && setState({ status: "error", message: error instanceof ApiError ? error.message : "Correlation unavailable" }));
    return () => { cancelled = true; };
  }, [range, window, reloadKey]);

  const d = state.status === "ready" ? state.data : null;
  const full = d?.full_sample;
  const rolling = (d?.rolling ?? []).filter((p) => p.correlation !== null);

  return (
    <section className="history-section" aria-label="Copper–Kwacha correlation">
      <div className="panel-heading"><span>COPPER–KWACHA · CORRELATION</span></div>
      <p className="notice">
        Pearson correlation is calculated from month-over-month percentage changes in Copper USD and USD/ZMW, not from their price levels. Correlation measures co-movement in this stored sample. It does not establish that Copper causes, explains or predicts movements in the Kwacha. The sample reflects only the overlap of stored IMF Copper and stored BoZ FX history. COPPER ZMW is not used, because it is the product of the two inputs.
      </p>
      {state.status === "loading" && <p className="notice">Calculating from stored data…</p>}
      {state.status === "error" && <p className="notice notice--error" role="alert">{state.message}. No correlation is shown.</p>}
      {d && full && (
        <>
          <dl className="quote-grid" aria-label="Full-sample correlation">
            <div><dt>PEARSON r</dt><dd className="quote-mid">{full.correlation !== null ? r3(full.correlation) : "—"}</dd></div>
            <div><dt>N</dt><dd>{full.sample_size} monthly change{full.sample_size === 1 ? "" : "s"}</dd></div>
            <div><dt>CHANGES COVERED</dt><dd>{full.first_change_month && full.last_change_month ? `${formatMonth(full.first_change_month)} to ${formatMonth(full.last_change_month)}` : "—"}</dd></div>
          </dl>
          {full.status !== "available" && full.reason && <p className="notice">{full.reason}</p>}
          {full.status === "available" && full.small_sample && <p className="notice text-stale">{SMALL_SAMPLE}</p>}
          <p className="notice">
            {d.matched_months} matched months, {d.candidate_changes} adjacent month pairs, {d.changes_used} used. Excluded: {d.changes_excluded_gap} across a missing month, {d.changes_excluded_partial} touching the partial FX source-start month, {d.changes_excluded_nonpositive} with a nonpositive analytical input (stored values are unchanged).
          </p>
          <div className="range-row" role="group" aria-label="Rolling window">
            {CORRELATION_WINDOWS.map((item) => (
              <button key={item} type="button" className={item === window ? "range range--active" : "range"}
                aria-pressed={item === window} onClick={() => setWindow(item)}>{item}</button>
            ))}
          </div>
          <p className="notice">Window = number of consecutive monthly change observations ({d.window_changes} changes need {d.window_changes + 1} consecutive monthly levels). A window never crosses a missing month, the partial FX month or an excluded change.</p>
          <div className="panel-heading"><span>ROLLING CORRELATION ({window})</span></div>
          {rolling.length === 0 && <p className="notice">{d.rolling_reason ?? "No rolling windows are available for this range."}</p>}
          {rolling.length === 1 && <p className="notice">Only one rolling window is available, so no line is drawn; see the table.</p>}
          {rolling.length > 1 && (
            <AuctionChart
              points={rolling.map((p) => ({ auction_date: p.window_end_month, yield_rate: p.correlation as string }))}
              label={`Rolling ${window} Pearson correlation of monthly changes in Copper USD and USD/ZMW, between -1 and +1`}
              precision={3} formatPrice={(v) => v.toFixed(2)} monthly markers />
          )}
          {rolling.length > 0 && (
            <div className="table-scroll">
              <table>
                <thead><tr><th>END MONTH</th><th>PEARSON r</th><th>N</th></tr></thead>
                <tbody>
                  {rolling.map((p) => (
                    <tr key={p.window_end_month}><td>{formatMonth(p.window_end_month)}</td><td>{r3(p.correlation as string)}</td><td>{p.sample_size}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="notice">No significance test, strength label, regression or sensitivity is calculated. {d.methodology}</p>
        </>
      )}
    </section>
  );
}
