import { useEffect, useState } from "react";
import { ApiError, getCopperKwachaSensitivity, type CopperKwachaSensitivity, type CopperRange } from "./api";
import { formatMonth } from "./format";

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: CopperKwachaSensitivity };

const SMALL_SAMPLE = "Small sample: this sensitivity estimate is based on fewer than 12 monthly changes and may be unstable.";

/** Sensitivity section of the COPPER FX view. The statistic is calculated by the API; the browser only displays it. */
export default function CopperKwachaSensitivitySection({ range, reloadKey }: { range: CopperRange; reloadKey: number }) {
  const [state, setState] = useState<State>({ status: "loading" });
  useEffect(() => {
    let cancelled = false; // a late response for an older range must not overwrite the current one
    setState({ status: "loading" });
    getCopperKwachaSensitivity(range)
      .then((data) => !cancelled && setState({ status: "ready", data }))
      .catch((error: unknown) =>
        !cancelled && setState({ status: "error", message: error instanceof ApiError ? error.message : "Sensitivity unavailable" }));
    return () => { cancelled = true; };
  }, [range, reloadKey]);

  const d = state.status === "ready" ? state.data : null;
  return (
    <section className="history-section" aria-label="Copper–Kwacha sensitivity">
      <div className="panel-heading"><span>COPPER–KWACHA · SENSITIVITY</span></div>
      <p className="notice">
        β is the covariance of monthly USD/ZMW changes with monthly Copper USD changes divided by the variance of Copper changes, calculated on the same eligible monthly changes as the correlation. It is a descriptive statistic for the stored sample, not a causal estimate or forecast.
      </p>
      <p className="notice">
        USD/ZMW is quoted as kwacha per US dollar. A positive USD/ZMW change means the kwacha depreciated against the dollar; a negative change means it appreciated.
      </p>
      {state.status === "loading" && <p className="notice">Calculating from stored data…</p>}
      {state.status === "error" && <p className="notice notice--error" role="alert">{state.message}. No sensitivity is shown.</p>}
      {d && (
        <>
          <dl className="quote-grid" aria-label="Historical sensitivity">
            <div><dt>HISTORICAL SENSITIVITY β</dt><dd className="quote-mid">{d.beta !== null ? Number(d.beta).toFixed(4) : "—"}</dd></div>
            <div><dt>N</dt><dd>{d.sample_size} monthly change{d.sample_size === 1 ? "" : "s"}</dd></div>
            <div><dt>CHANGES COVERED</dt><dd>{d.first_change_month && d.last_change_month ? `${formatMonth(d.first_change_month)} to ${formatMonth(d.last_change_month)}` : "—"}</dd></div>
          </dl>
          <p className="notice">Units: fractional USD/ZMW change per unit fractional Copper USD change.</p>
          {d.status === "insufficient_sample" && <p className="notice">Not enough eligible monthly changes yet. {d.reason}</p>}
          {d.status === "zero_copper_variance" && <p className="notice">β is undefined because the Copper USD change series has zero variance in this sample. It is not zero.</p>}
          {d.status === "available" && d.small_sample && <p className="notice text-stale">{SMALL_SAMPLE}</p>}
          <p className="notice">
            {d.matched_months} matched months, {d.candidate_changes} adjacent month pairs, {d.changes_used} used. Excluded: {d.changes_excluded_gap} across a missing month, {d.changes_excluded_partial} touching the partial FX source-start month, {d.changes_excluded_nonpositive} with a nonpositive analytical input (stored values are unchanged). No intercept, fit, R², significance test or forecast is calculated.
          </p>
        </>
      )}
    </section>
  );
}
