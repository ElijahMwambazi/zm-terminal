import { forwardRef, useEffect, useMemo, useState } from "react";
import {
  ApiError, ENERGY_GROUP_LABEL, ENERGY_GROUP_SHORT, ENERGY_STATION_GROUPS, EnergyNotFoundError, getEnergyGroupHistory, getEnergyStationHistory,
  type EnergyGenerationGroupHistory, type EnergyGenerationHistory, type EnergyGenerationObservation, type EnergyStationGroup,
} from "./api";
import EnergyAnnualChart from "./EnergyAnnualChart";
import type { EnergyState } from "./useEnergyGeneration";

type Load<T> = { status: "loading" } | { status: "error"; message: string } | { status: "ready"; data: T };
type Selected = { group: EnergyStationGroup; name: string };

const sameStation = (a: Selected | null, b: Selected) => !!a && a.group === b.group && a.name === b.name;
const message = (e: unknown) => (e instanceof ApiError ? e.message : "History unavailable");
/** Exact stored string (never reformatted) with its unit; a reported 0.00 stays "0.00 GWh". */
export const gwh = (value: string) => `${value} GWh`;
export function rightsText(status: string) {
  return `Stored ERB data. rights_status = ${status}. Public redistribution is not represented as cleared.`;
}

function useStationHistory(sel: Selected | null) {
  const [state, setState] = useState<Load<EnergyGenerationHistory>>({ status: "loading" });
  const group = sel?.group, name = sel?.name;
  useEffect(() => {
    if (!group || name === undefined) return;
    let current = true; // a later selection flips this, so a slow earlier response is dropped
    setState({ status: "loading" });
    getEnergyStationHistory(group, name)
      .then((data) => current && setState({ status: "ready", data }))
      .catch((e: unknown) => current && setState({ status: "error", message: e instanceof EnergyNotFoundError ? e.message : message(e) }));
    return () => { current = false; };
  }, [group, name]);
  return state;
}

function useGroupHistory(group: EnergyStationGroup) {
  const [state, setState] = useState<Load<EnergyGenerationGroupHistory>>({ status: "loading" });
  useEffect(() => {
    let current = true;
    setState({ status: "loading" });
    getEnergyGroupHistory(group)
      .then((data) => current && setState({ status: "ready", data }))
      .catch((e: unknown) => current && setState({ status: "error", message: message(e) }));
    return () => { current = false; };
  }, [group]);
  return state;
}

function StationCell({ s }: { s: EnergyGenerationObservation }) {
  return s.value_state === "reported" && s.generation_gwh !== null
    ? <>{gwh(s.generation_gwh)}</>
    : <span className="muted" title="Printed as a dash in the source; not zero">— NOT REPORTED</span>;
}

const EnergyGenerationDetail = forwardRef<HTMLElement, { state: EnergyState; onRefresh: () => void }>(
  function EnergyGenerationDetail({ state, onRefresh }, ref) {
    const snapshot = state.status === "ready" ? state.data.snapshot : null;
    const [selected, setSelected] = useState<Selected | null>(null);
    const [groupPick, setGroupPick] = useState<EnergyStationGroup | null>(null);

    // Keep the exact (group, name) identity across refreshes; otherwise fall back to the first station.
    useEffect(() => {
      setSelected((prev) => {
        const stations = snapshot?.stations ?? [];
        if (stations.length === 0) return null;
        const keep = stations.find((s) => sameStation(prev, { group: s.station_group, name: s.station_name }));
        const pick = keep ?? stations[0];
        return keep && prev ? prev : { group: pick.station_group, name: pick.station_name };
      });
    }, [snapshot]);

    const group = groupPick ?? selected?.group ?? ENERGY_STATION_GROUPS[0];
    const stationHistory = useStationHistory(snapshot ? selected : null);
    const groupHistory = useGroupHistory(group);
    const totals = useMemo(() => new Map((snapshot?.group_totals ?? []).map((t) => [t.station_group, t])), [snapshot]);

    return (
      <article className="panel detail-panel" ref={ref} tabIndex={-1} aria-label="Electricity generation detail">
        <div className="panel-heading">
          <span>POWER ZM · ELECTRICITY GENERATION <span className="muted">ERB · ANNUAL · STORED</span></span>
          <button type="button" className="link-button" onClick={onRefresh} disabled={state.status === "loading"} title="Re-reads stored data; nothing is fetched from ERB">
            {state.status === "loading" ? "LOADING…" : "RE-READ STORED"}
          </button>
        </div>
        <div className="detail-body">
          {state.status === "loading" && <p className="notice">Loading stored ERB generation…</p>}
          {state.status === "error" && <p className="notice notice--error" role="alert">{state.message}. No Energy values are shown.</p>}
          {state.status === "ready" && !snapshot && <p className="notice">No stored ERB generation.</p>}
          {snapshot && (
            <>
              <section aria-label="Publication overview">
                <dl className="quote-grid">
                  <div><dt>OBSERVATION YEAR</dt><dd className="quote-mid">{snapshot.observation_year}</dd></div>
                  <div><dt>PUBLICATION</dt><dd>{snapshot.publication_year} · r{snapshot.revision_sequence}</dd></div>
                  <div><dt>SOURCE</dt><dd>{snapshot.publisher}</dd></div>
                  <div><dt>STATIONS</dt><dd>{snapshot.stations.length}</dd></div>
                  <div><dt>GROUP TOTALS</dt><dd>{snapshot.group_totals.length}</dd></div>
                </dl>
                <p className="notice energy-wrap">{snapshot.title}{snapshot.cover_date ? ` · ${snapshot.cover_date}` : ""}</p>
              </section>

              <section aria-label="Published group totals">
                <div className="panel-heading"><span>PUBLISHED ERB GROUP TOTALS · {snapshot.observation_year}</span></div>
                <div className="energy-totals">
                  {ENERGY_STATION_GROUPS.map((g) => {
                    const t = totals.get(g);
                    return (
                      <div key={g} className="energy-total" title={t ? `${t.source_table} · PDF p.${t.source_pdf_page} · printed p.${t.source_printed_page}` : undefined}>
                        <span className="energy-total-label"><span className="energy-long">{ENERGY_GROUP_LABEL[g]}</span><span className="energy-short">{ENERGY_GROUP_SHORT[g]}</span></span>
                        <strong>{t ? gwh(t.total_gwh) : "—"}</strong>
                        <small className="muted">{t ? "published total" : "not returned"}</small>
                      </div>
                    );
                  })}
                </div>
                <p className="notice">Figures printed by ERB for each group. They are not summed, recomputed or combined into a national total here.</p>
              </section>

              <section aria-label="Station generation">
                <div className="panel-heading"><span>STATIONS · {snapshot.observation_year}</span></div>
                {snapshot.stations.length === 0 ? <p className="notice">No station rows were returned.</p> : (
                  <div className="table-scroll">
                    <table>
                      <thead><tr><th>STATION</th><th>GROUP</th><th>GENERATION</th><th>SOURCE</th></tr></thead>
                      <tbody>
                        {snapshot.stations.map((s) => {
                          const active = sameStation(selected, { group: s.station_group, name: s.station_name });
                          return (
                            <tr key={`${s.station_group}|${s.station_name}`} className={active ? "row--focus" : undefined}>
                              <td className="energy-station">
                                <button type="button" className="link-button energy-pick" aria-pressed={active}
                                  onClick={() => { setSelected({ group: s.station_group, name: s.station_name }); setGroupPick(null); }}>{s.station_name}</button>
                              </td>
                              <td>{ENERGY_GROUP_LABEL[s.station_group]}</td>
                              <td><StationCell s={s} /></td>
                              <td>{s.source_table} · p.{s.source_printed_page}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>

              <section className="history-section" aria-label="Station annual history">
                <div className="panel-heading"><span>STATION HISTORY{selected ? ` · ${selected.name} · ${ENERGY_GROUP_LABEL[selected.group]}` : ""}</span></div>
                {!selected && <p className="notice">Select a station.</p>}
                {selected && stationHistory.status === "loading" && <p className="notice">Loading stored station history…</p>}
                {selected && stationHistory.status === "error" && <p className="notice notice--error" role="alert">{stationHistory.message}. No station history is shown.</p>}
                {selected && stationHistory.status === "ready" && <HistoryBody h={stationHistory.data} />}
              </section>

              <section className="history-section" aria-label="Group total history">
                <div className="panel-heading"><span>PUBLISHED GROUP TOTAL HISTORY · {ENERGY_GROUP_LABEL[group]}</span></div>
                <div className="range-row" role="group" aria-label="Station group">
                  {ENERGY_STATION_GROUPS.map((g) => (
                    <button key={g} type="button" className={g === group ? "range range--active" : "range"} aria-pressed={g === group} onClick={() => setGroupPick(g)}>
                      <span className="energy-long">{ENERGY_GROUP_LABEL[g]}</span><span className="energy-short">{ENERGY_GROUP_SHORT[g]}</span>
                    </button>
                  ))}
                </div>
                {groupHistory.status === "loading" && <p className="notice">Loading stored group history…</p>}
                {groupHistory.status === "error" && <p className="notice notice--error" role="alert">{groupHistory.message}. No group history is shown.</p>}
                {groupHistory.status === "ready" && <GroupBody h={groupHistory.data} />}
              </section>

              <section aria-label="Source and rights">
                <div className="panel-heading"><span>SOURCE · RIGHTS</span><span className="muted">LOCAL / INTERNAL · RIGHTS REVIEW REQUIRED</span></div>
                <p className="notice">{rightsText(snapshot.rights_status)}</p>
                <ul className="provenance">
                  <li><span>Source</span><a href={snapshot.source_url} target="_blank" rel="noreferrer" className="energy-wrap">{snapshot.source_url}</a></li>
                  <li><span>Series</span>{snapshot.source_series}</li>
                  <li><span>Use</span>Stored annual data read from this application's database; the terminal never contacts ERB.</li>
                </ul>
              </section>
            </>
          )}
        </div>
      </article>
    );
  },
);

function HistoryBody({ h }: { h: EnergyGenerationHistory }) {
  const reported = h.observations.filter((p) => p.value_state === "reported").length;
  const points = useMemo(() => h.observations.map((p) => ({ year: p.observation_year, value: p.generation_gwh })), [h]);
  if (h.observations.length === 0) return <p className="notice">No stored annual values for this station.</p>;
  return (
    <>
      <p className="notice">
        {h.count} stored years, {h.observations[0].observation_year} to {h.observations[h.count - 1].observation_year}. {rightsText(h.rights_status)}
      </p>
      {reported === 0 && <p className="notice">No reported values: every stored year is not reported (printed as a dash in the source).</p>}
      {reported === 1 && <p className="notice">Only one reported year; there is no line to draw.</p>}
      {reported > 0 && <EnergyAnnualChart points={points} label={`${h.station_name} annual generation, GWh; years not reported are gaps`} />}
      <details className="monthly-source" open>
        <summary>Annual values and provenance</summary>
        <div className="table-scroll monthly-table-scroll">
          <table>
            <thead><tr><th>YEAR</th><th>GWh</th><th>PUBLICATION</th><th>SOURCE</th></tr></thead>
            <tbody>
              {h.observations.map((p) => (
                <tr key={p.observation_year}>
                  <td>{p.observation_year}</td>
                  <td>{p.value_state === "reported" && p.generation_gwh !== null ? p.generation_gwh : <span className="muted">— NOT REPORTED</span>}</td>
                  <td>{p.publication_year} r{p.revision_sequence}</td>
                  <td>{p.source_table} · p.{p.source_printed_page}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </>
  );
}

function GroupBody({ h }: { h: EnergyGenerationGroupHistory }) {
  const points = useMemo(() => h.observations.map((p) => ({ year: p.observation_year, value: p.total_gwh })), [h]);
  if (h.observations.length === 0) return <p className="notice">No stored published totals for {ENERGY_GROUP_LABEL[h.station_group]}.</p>;
  return (
    <>
      <p className="notice">
        {h.count} stored years, {h.observations[0].observation_year} to {h.observations[h.count - 1].observation_year}. Published ERB totals, not calculated from stations.
        {h.rights_status ? ` ${rightsText(h.rights_status)}` : ""}
      </p>
      {h.count > 1 && <EnergyAnnualChart points={points} label={`${ENERGY_GROUP_LABEL[h.station_group]} published annual total, GWh`} color="#7cc4ff" />}
      <details className="monthly-source" open>
        <summary>Published totals and provenance</summary>
        <div className="table-scroll monthly-table-scroll">
          <table>
            <thead><tr><th>YEAR</th><th>GWh</th><th>PUBLICATION</th><th>SOURCE</th></tr></thead>
            <tbody>
              {h.observations.map((p) => (
                <tr key={p.observation_year}>
                  <td>{p.observation_year}</td><td>{p.total_gwh}</td><td>{p.publication_year} r{p.revision_sequence}</td>
                  <td>{p.source_table} · p.{p.source_printed_page}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </>
  );
}

export default EnergyGenerationDetail;
