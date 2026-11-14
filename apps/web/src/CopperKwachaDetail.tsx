import { forwardRef, useEffect, useMemo, useState } from "react";
import { ApiError, COPPER_RANGES, getCopperZmHistory, type CopperRange, type CopperZmHistory } from "./api";
import CopperKwachaCorrelationSection from "./CopperKwachaCorrelation";
import CopperKwachaCompareChart, { type CompareSeries } from "./CopperKwachaCompareChart";
import { rebaseTo100 } from "./compareIndex";
import { formatFixed, formatMonth } from "./format";

const SERIES = [
  { key: "copper", label: "COPPER USD", color: "#ffb000" },
  { key: "fx", label: "USD/ZMW", color: "#7cc4ff" },
  { key: "zmw", label: "COPPER ZMW", color: "#63d68f" },
] as const;

type History =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: CopperZmHistory };

/** Matched monthly comparison over the existing COPPER ZM history response (one request, loaded only when this view is open). */
const CopperKwachaDetail = forwardRef<HTMLElement, object>(function CopperKwachaDetail(_props, ref) {
  const [range, setRange] = useState<CopperRange>("1Y");
  const [history, setHistory] = useState<History>({ status: "loading" });
  const [reloadKey, setReloadKey] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setHistory({ status: "loading" });
    getCopperZmHistory(range)
      .then((data) => !cancelled && setHistory({ status: "ready", data }))
      .catch((error: unknown) =>
        !cancelled && setHistory({ status: "error", message: error instanceof ApiError ? error.message : "Comparison data unavailable" }));
    return () => { cancelled = true; };
  }, [range, reloadKey]);

  const points = history.status === "ready" ? history.data.observations : [];
  const indexed = useMemo(() => {
    if (points.length < 2) return null;
    const lines = [rebaseTo100(points.map((p) => p.copper_usd_per_mt)), rebaseTo100(points.map((p) => p.usd_zmw_observed_days_avg)),
      rebaseTo100(points.map((p) => p.zmw_per_mt))];
    if (lines.some((line) => line === null)) return null;
    return SERIES.map((s, i): CompareSeries => ({ ...s, values: lines[i] as number[] }));
  }, [points]);
  const months = useMemo(() => points.map((p) => p.period_start), [points]);
  const last = (line: number[]) => line[line.length - 1].toFixed(1);

  return (
    <article className="panel detail-panel" ref={ref} tabIndex={-1} aria-label="COPPER–KWACHA comparison">
      <div className="panel-heading">
        <span>COPPER–KWACHA · COMPARISON</span>
        <button type="button" className="link-button" onClick={() => setReloadKey((k) => k + 1)} disabled={history.status === "loading"}>
          {history.status === "loading" ? "LOADING…" : "RE-READ STORED"}
        </button>
      </div>
      <div className="detail-body">
        <p className="notice notice--derived">
          <span className="badge-derived" title="ZM Terminal analytical view, not an official series">ANALYTICAL</span>{" "}
          COPPER USD · USD/ZMW · COPPER ZMW. Matched monthly comparison of the IMF Copper benchmark, the stored BoZ USD/ZMW observed-days average and the derived COPPER ZM value. The chart rebases each series to 100 at the selected range's first matched month so differently scaled quantities can be compared visually.
        </p>
        <p className="notice">COPPER ZM is mechanically the product of Copper USD and USD/ZMW, so its movement reflects changes in both; the three series are not independent. Co-movement shown here is descriptive and not evidence of causation. Local/internal use: public display, API redistribution and commercial use of the IMF-derived series remain under review.</p>
        <div className="range-row" role="group" aria-label="Comparison range">
          {COPPER_RANGES.map((item) => (
            <button key={item} type="button" className={item === range ? "range range--active" : "range"}
              aria-pressed={item === range} onClick={() => setRange(item)}>{item}</button>
          ))}
        </div>
        {history.status === "loading" && <p className="notice">Loading matched monthly data…</p>}
        {history.status === "error" && <p className="notice notice--error" role="alert">{history.message}. No comparison is shown.</p>}
        {history.status === "ready" && points.length === 0 && (
          <p className="notice">No months to compare in this range: a month needs both a stored IMF Copper average and eligible stored BoZ USD/ZMW observations.
            {history.data.copper_months_without_fx > 0 && <> {history.data.copper_months_without_fx} stored Copper month(s) in this range have no eligible stored BoZ FX and are omitted.</>}</p>
        )}
        {history.status === "ready" && points.length > 0 && (
          <p className="derived-calc derived-support" aria-label="Matched months summary">
            {points.length} matched month{points.length === 1 ? "" : "s"} · {formatMonth(points[0].period_start)} to {formatMonth(points[points.length - 1].period_start)}
          </p>
        )}
        {history.status === "ready" && points.length === 1 && (
          <p className="notice">Only one matched month in this range: at least two are needed to compare movement, so no chart is drawn. The stored values are in the table below.</p>
        )}
        {history.status === "ready" && points.length > 1 && !indexed && (
          <p className="notice">The indexed comparison is unavailable for this range because a series' first matched value is zero (or not a finite number), so it cannot be rebased to 100. The stored values are in the table below.</p>
        )}
        {indexed && (
          <section aria-label="Indexed comparison chart">
            <ul className="curve-legend" aria-label="Chart legend">
              {SERIES.map((s, i) => (
                <li key={s.key} style={{ display: "inline", marginRight: 14 }}>
                  <span className="legend-mark" style={{ background: s.color }} aria-hidden="true" /> {s.label} (latest index {last(indexed[i].values)})
                </li>
              ))}
            </ul>
            <CopperKwachaCompareChart months={months} series={indexed}
              label="Copper in US dollars, USD/ZMW and Copper in kwacha, each indexed to 100 at the first matched month of the selected range" />
            <p className="notice">
              Indexed to 100 at the first matched month in the selected range. This is a visual comparison of relative movement, not an official index. Only months with both stored IMF Copper and eligible stored BoZ FX observations are included; the lines join matched months and no month is filled or interpolated. COPPER ZM mechanically combines the other two series, so its movement reflects changes in both the Copper benchmark and USD/ZMW. This chart is descriptive and does not establish economic causality.
            </p>
          </section>
        )}
        {history.status === "ready" && points.length > 0 && (
          <>
            <div className="table-scroll">
              <table>
                <thead><tr><th>MONTH</th><th>COPPER USD/mt</th><th>USD/ZMW OBS-DAYS AVG</th><th>COPPER ZMW/mt</th><th>BOZ DAYS</th><th>FX OBS</th></tr></thead>
                <tbody>
                  {points.map((p) => (
                    <tr key={p.period_start}>
                      <td>{formatMonth(p.period_start)}{p.fx_coverage === "partial_source_window" && <span className="text-stale"> · partial</span>}</td>
                      <td>{formatFixed(p.copper_usd_per_mt, 2, true)}</td><td>{formatFixed(p.usd_zmw_observed_days_avg, 4)}</td>
                      <td>{formatFixed(p.zmw_per_mt, 2, true)}</td><td>{p.fx_day_count}</td><td>{p.fx_observation_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="notice">
              Values are the stored and derived figures from the COPPER ZM history, shown rounded for display. {points.some((p) => p.fx_coverage === "partial_source_window") && <>Months marked partial are those in which the stored BoZ history began, so the FX component covers only part of the month. </>}
              BOZ DAYS and FX OBS show how much stored BoZ data supports each month. Months before stored BoZ history cannot be compared, and no value is filled for them.
              {history.data.copper_months_without_fx > 0 && <> {history.data.copper_months_without_fx} stored Copper month(s) in this selected range have no eligible stored BoZ FX and are omitted.</>}
            </p>
          </>
        )}
        <CopperKwachaCorrelationSection range={range} reloadKey={reloadKey} />
      </div>
    </article>
  );
});

export default CopperKwachaDetail;
