import { useEffect, useMemo, useState } from "react";
import { MONTHLY_RANGES, type MonthlyRange } from "./api";
import { formatYearMonth } from "./format";
import MonthlyFxChart, { RATE_STYLE, type RateKey } from "./MonthlyFxChart";
import { useUsdZmwMonthly } from "./useUsdZmwMonthly";

const RATE_KEYS: RateKey[] = ["mid", "buy", "sell"];

const ANNUAL_SERIES = "annual_report_table_13_monthly_average";
const FORTNIGHTLY_SERIES = "fortnightly_table_02_monthly_average";
const FIRST_ZMW_MONTH = "2013-01"; // before this, BoZ printed old kwacha (ZMK); the API sends canonical ZMW (printed / 1000)

/** Source labels follow the stored source series; an unknown series gets neutral wording, never another source's table. */
function sourceDetails(series: string): { table: string; link: string } {
  if (series === ANNUAL_SERIES) return { table: "Table 13", link: "Open Annual Report PDF" };
  if (series === FORTNIGHTLY_SERIES) return { table: "Table 02", link: "Open Fortnightly Statistics PDF" };
  return { table: "official BoZ table", link: "Open source PDF" };
}

/** Official BoZ monthly averages: historical, discrete, stored. Independent of the live quote above it; fails on its own. */
export default function BozMonthlyFx() {
  const [range, setRange] = useState<MonthlyRange>("1Y");
  const [active, setActive] = useState<RateKey[]>(["mid"]);
  const [selected, setSelected] = useState<string | null>(null);
  const state = useUsdZmwMonthly(range);

  const data = state.status === "ready" ? state.data : state.status === "loading" ? state.previous : null;
  const observations = data?.observations;
  const loading = state.status === "loading";

  // A newly loaded range starts on its latest month.
  useEffect(() => { if (state.status === "ready") setSelected(state.data.last_month); }, [state]);

  const latest = data && observations?.length ? observations[observations.length - 1] : null;
  // Until a month is chosen (or when the choice is not in this range) the readout shows the latest month.
  const index = useMemo(() => {
    if (!observations?.length) return -1;
    const found = observations.findIndex((o) => o.month === selected);
    return found >= 0 ? found : observations.length - 1;
  }, [observations, selected]);
  const point = observations && index >= 0 ? observations[index] : null;
  const details = sourceDetails(point?.source_series ?? "");

  const toggle = (key: RateKey) =>
    setActive((current) => current.includes(key) ? (current.length > 1 ? current.filter((k) => k !== key) : current) : RATE_KEYS.filter((k) => k === key || current.includes(k)));
  const step = (delta: number) => observations && setSelected(observations[index + delta]?.month ?? selected);

  return (
    <section className="history-section monthly-fx" aria-labelledby="boz-monthly-heading" aria-busy={loading}>
      <div className="monthly-head">
        <h3 id="boz-monthly-heading" className="monthly-title">BoZ Monthly Average</h3>
        <span className="badge badge--monthly">MONTHLY · NOT LIVE</span>
      </div>
      <p className="monthly-sub">Official Bank of Zambia monthly averages (Annual Reports for 2010-2013, Fortnightly Statistics from 2014), ZMW per USD. Historical, one value per month.</p>

      <div className="range-row" role="group" aria-label="Monthly range">
        {MONTHLY_RANGES.map((item) => (
          <button key={item} type="button" className={item === range ? "range range--active" : "range"}
            aria-pressed={item === range} onClick={() => setRange(item)}>{item}</button>
        ))}
        <span className="range-gap" aria-hidden="true" />
        <div className="rate-toggles" role="group" aria-label="Series">
          {RATE_KEYS.map((key) => (
            <button key={key} type="button" className={active.includes(key) ? "range range--series range--active" : "range range--series"}
              aria-pressed={active.includes(key)} onClick={() => toggle(key)}>
              <i className="swatch" style={{ background: RATE_STYLE[key].color }} aria-hidden="true" />{RATE_STYLE[key].label}
            </button>
          ))}
        </div>
      </div>

      <p className="monthly-pending" role="status">
        {state.status === "loading" && state.previous && `Loading ${range}… showing previous ${state.previous.range} data`}
      </p>

      {state.status === "error" && (
        <p className="notice notice--error" role="alert">Historical BoZ monthly data could not be loaded ({state.message}). The live quote above is unaffected.</p>
      )}
      {state.status === "loading" && !data && <div className="monthly-skeleton" role="status">Loading monthly averages…</div>}
      {data && data.observations.length === 0 && <p className="notice">No monthly observations are available.</p>}

      {data && observations && observations.length > 0 && latest && (
        <div className={loading ? "monthly-body monthly-body--loading" : "monthly-body"}>
          <dl className="monthly-summary">
            <div><dt>Latest monthly average</dt><dd className="monthly-latest">{latest.mid}</dd><dd className="monthly-dim">{formatYearMonth(data.last_month ?? latest.month)}</dd></div>
            <div><dt>Displayed range · {data.range}</dt><dd>{formatYearMonth(data.first_month ?? observations[0].month)} – {formatYearMonth(data.last_month ?? latest.month)}</dd><dd className="monthly-dim">{data.count} observations</dd></div>
          </dl>

          <MonthlyFxChart observations={observations} active={active} selectedMonth={selected} onInspect={setSelected} />

          {point && (
            <div className="monthly-readout" aria-live="polite">
              <div className="monthly-readout-main">
                <button type="button" className="link-button" aria-label="Previous month" disabled={index <= 0} onClick={() => step(-1)}>‹</button>
                <strong>{formatYearMonth(point.month)}</strong>
                <button type="button" className="link-button" aria-label="Next month" disabled={index >= observations.length - 1} onClick={() => step(1)}>›</button>
                <span>Buy <b>{point.buy}</b></span>
                <span>Mid <b>{point.mid}</b></span>
                <span>Sell <b>{point.sell}</b></span>
                <span className="monthly-dim">Bank of Zambia</span>
              </div>
              <details className="monthly-source">
                <summary>Source details</summary>
                <ul className="provenance">
                  <li><span>Source</span>Bank of Zambia, official monthly average</li>
                  <li><span>Publication</span>{point.source_publication}</li>
                  <li><span>Page</span>{point.source_page} ({details.table})</li>
                  {point.month < FIRST_ZMW_MONTH
                    ? <li><span>Values</span>Normalized to ZMW from the published ZMK values (exact division by 1000); published with {point.published_precision} decimal places in ZMK</li>
                    : <li><span>Precision</span>{point.published_precision} decimal places, as published</li>}
                  <li><span>Document</span><a href={point.source_url} target="_blank" rel="noopener noreferrer">{details.link}</a></li>
                  <li><span>Series</span>{point.source_series}</li>
                </ul>
              </details>
            </div>
          )}

          <details className="monthly-table">
            <summary>Data table ({observations.length} months)</summary>
            <div className="table-scroll monthly-table-scroll">
              <table>
                <caption className="sr-only">BoZ monthly average USD/ZMW</caption>
                <thead><tr><th scope="col">Month</th><th scope="col">Buy</th><th scope="col">Mid</th><th scope="col">Sell</th></tr></thead>
                <tbody>
                  {[...observations].reverse().map((o) => (
                    <tr key={o.month} className={o.month === selected ? "row--focus" : undefined}>
                      <th scope="row"><button type="button" className="link-button" onClick={() => setSelected(o.month)}>{formatYearMonth(o.month)}</button></th>
                      <td>{o.buy}</td><td>{o.mid}</td><td>{o.sell}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </div>
      )}
    </section>
  );
}
