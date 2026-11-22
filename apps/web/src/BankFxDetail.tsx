import { forwardRef, useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getBankFxDate, type BankFxInstitutionRate, type BankFxSlot, type BankFxSnapshot } from "./api";
import { formatDate, formatLusaka } from "./format";
import type { BankFxState } from "./useBankFx";

/** Only the exact stored state review_required gets the review wording; any other value is shown neutrally with its literal status. */
export const bankFxRightsHeading = (status: string) =>
  status === "review_required" ? "LOCAL / INTERNAL · RIGHTS REVIEW REQUIRED" : "LOCAL / INTERNAL · RIGHTS STATUS";
export const bankFxRightsText = (status: string) =>
  `Stored for local/internal use. rights_status = ${status}. Public display, API redistribution and commercial reuse are not represented as cleared by this interface.`;

type View = { kind: "latest" } | { kind: "date"; day: string };
type Dated = { status: "idle" } | { status: "loading" } | { status: "ready"; snapshot: BankFxSnapshot };

const RATE_FIELDS = ["interbank_buy", "interbank_sell", "retail_buy", "retail_sell"] as const;
const latestSlot = (s: BankFxSnapshot): BankFxSlot => s.slots[s.slots.length - 1].slot;
const message = (e: unknown) => (e instanceof ApiError ? e.message : "ZM Terminal API is unavailable");

function MarketAverage({ average }: { average: BankFxInstitutionRate | null }) {
  return (
    <section aria-label="BoZ-published market average">
      <div className="panel-heading"><span>BOZ-PUBLISHED MARKET AVERAGE</span><span className="muted">source row, not calculated here</span></div>
      {average === null ? <p className="notice">Market average not published for this slot.</p> : (
        <dl className="quote-grid bank-fx-average">
          <div><dt>INTERBANK BUY</dt><dd className="bank-fx-rate">{average.interbank_buy}</dd></div>
          <div><dt>INTERBANK SELL</dt><dd className="bank-fx-rate">{average.interbank_sell}</dd></div>
          <div><dt>RETAIL BUY</dt><dd className="bank-fx-rate">{average.retail_buy}</dd></div>
          <div><dt>RETAIL SELL</dt><dd className="bank-fx-rate">{average.retail_sell}</dd></div>
        </dl>
      )}
    </section>
  );
}

const BankFxDetail = forwardRef<HTMLElement, { state: BankFxState; onRefresh: () => void }>(
  function BankFxDetail({ state, onRefresh }, ref) {
    const latest = state.status === "ready" ? state.data.snapshot : null;
    const [view, setView] = useState<View>({ kind: "latest" });
    const [dated, setDated] = useState<Dated>({ status: "idle" });
    const [notice, setNotice] = useState<{ tone: "info" | "error"; text: string } | null>(null);
    const [dateInput, setDateInput] = useState("");
    const [slot, setSlot] = useState<BankFxSlot | null>(null);
    const generation = useRef(0);
    useEffect(() => () => { generation.current += 1; }, []); // unmount invalidates any pending date read

    const shown = view.kind === "latest" ? latest : dated.status === "ready" ? dated.snapshot : null;
    const shownDate = shown?.observation_date ?? null;
    useEffect(() => { if (shownDate) setDateInput(shownDate); }, [shownDate]);

    // Keep the selected slot when the new snapshot publishes it; otherwise the latest published slot. Slots are never borrowed.
    useEffect(() => {
      if (!shown) return;
      setSlot((prev) => (prev !== null && shown.slots.some((s) => s.slot === prev) ? prev : latestSlot(shown)));
    }, [shown]);

    const loadDate = useCallback((day: string) => {
      const id = ++generation.current;
      setDated((prev) => (prev.status === "ready" ? prev : { status: "loading" }));
      setNotice({ tone: "info", text: `Loading stored snapshot for ${day}…` });
      getBankFxDate(day)
        .then((snapshot) => {
          if (id !== generation.current) return;
          if (snapshot === null) {
            setNotice({ tone: "info", text: `No stored commercial-bank FX snapshot for ${day}. No fallback date was used.` });
            setDated((prev) => (prev.status === "loading" ? { status: "idle" } : prev));
            return;
          }
          setView({ kind: "date", day });
          setDated({ status: "ready", snapshot });
          setNotice(null);
        })
        .catch((e: unknown) => {
          if (id !== generation.current) return;
          setNotice({ tone: "error", text: `${message(e)}. The previously displayed snapshot, if any, is unchanged.` });
          setDated((prev) => (prev.status === "loading" ? { status: "idle" } : prev));
        });
    }, []);

    const useLatest = () => {
      generation.current += 1; // drop any pending exact-date read
      setView({ kind: "latest" });
      setDated({ status: "idle" });
      setNotice(null);
    };
    const reread = () => {
      if (view.kind === "date") loadDate(view.day);
      else onRefresh();
    };
    const busy = state.status === "loading" || (state.status === "ready" && !!state.refreshing) || dated.status === "loading";
    const valid = /^\d{4}-\d{2}-\d{2}$/.test(dateInput);
    const effectiveSlot = shown ? (slot !== null && shown.slots.some((s) => s.slot === slot) ? slot : latestSlot(shown)) : null;
    const active = shown?.slots.find((s) => s.slot === effectiveSlot) ?? null;

    return (
      <article className="panel detail-panel" ref={ref} tabIndex={-1} aria-label="Commercial bank FX detail">
        <div className="panel-heading">
          <span>BANK FX · COMMERCIAL BANK USD/ZMW <span className="muted">BoZ TABLE · STORED</span></span>
          <button type="button" className="link-button" onClick={reread} disabled={busy} title="Re-reads stored data; nothing is fetched from BoZ">
            {busy ? "LOADING…" : "RE-READ STORED"}
          </button>
        </div>
        <div className="detail-body">
          <p className="notice">
            Official Bank of Zambia commercial-bank table, shown as stored source observations. Opening or refreshing this view never contacts BoZ.
            It is not a live quote and not necessarily current executable dealer pricing. ZM Terminal selects, ranks and compares no rate.
            09:30 / 12:30 / 15:30 are BoZ source slot labels; ZM Terminal does not assign a source timezone to them.
          </p>
          {state.status === "loading" && <p className="notice">Loading stored Bank FX…</p>}
          {state.status === "error" && view.kind === "latest" && <p className="notice notice--error" role="alert">{state.message}. No Bank FX values are shown.</p>}
          {state.status === "ready" && !latest && view.kind === "latest" && <p className="notice">No stored commercial-bank FX.</p>}

          {(state.status === "ready" || view.kind === "date") && (
            <form className="bank-fx-controls" aria-label="Stored date" onSubmit={(e) => { e.preventDefault(); if (valid) loadDate(dateInput); }}>
              <label htmlFor="bank-fx-date">STORED DATE</label>
              <input id="bank-fx-date" type="date" value={dateInput} onChange={(e) => setDateInput(e.target.value)} />
              <button type="submit" className="link-button" disabled={!valid}>LOAD DATE</button>
              <button type="button" className="link-button" onClick={useLatest} disabled={view.kind === "latest" && dated.status !== "loading"}>LATEST STORED</button>
            </form>
          )}
          {notice && <p className={notice.tone === "error" ? "notice notice--error" : "notice"} role={notice.tone === "error" ? "alert" : "status"}>{notice.text}</p>}

          {shown && (
            <>
              <section aria-label="Snapshot overview">
                <dl className="quote-grid">
                  <div><dt>OBSERVATION DATE</dt><dd className="quote-mid">{formatDate(shown.observation_date)} <span className="muted">({shown.observation_date})</span></dd></div>
                  <div><dt>{view.kind === "latest" ? "LATEST STORED" : "STORED DATE"}</dt><dd>{shown.slots.length} published slot{shown.slots.length === 1 ? "" : "s"}</dd></div>
                  <div><dt>BoZ LAYOUT</dt><dd>{shown.table_layout}</dd></div>
                </dl>
              </section>

              <section aria-label="Slot">
                <div className="panel-heading"><span>SOURCE SLOT</span><span className="muted">BoZ slot labels, not timestamps</span></div>
                <div className="bank-fx-slots" role="group" aria-label="Source slot">
                  {shown.slots.map((s) => (
                    <button key={s.slot} type="button" className="link-button bank-fx-slot" aria-pressed={s.slot === effectiveSlot} onClick={() => setSlot(s.slot)}>{s.slot}</button>
                  ))}
                </div>
              </section>

              {active && (
                <>
                  <section aria-label={`Bank rates ${active.slot}`}>
                    <div className="panel-heading"><span>BANK RATES · SLOT {active.slot}</span><span className="muted">ZMW per USD · source row order</span></div>
                    {active.banks.length === 0 ? <p className="notice">No bank rows were returned for this slot.</p> : (
                      <div className="table-scroll">
                        <table className="bank-fx-table">
                          <thead>
                            <tr>
                              <th scope="col" rowSpan={2} className="bank-fx-bank">BANK</th>
                              <th scope="colgroup" colSpan={2}>INTERBANK</th>
                              <th scope="colgroup" colSpan={2}>RETAIL (BoZ: {active.retail_source_label})</th>
                            </tr>
                            <tr><th scope="col">BUY</th><th scope="col">SELL</th><th scope="col">BUY</th><th scope="col">SELL</th></tr>
                          </thead>
                          <tbody>
                            {active.banks.map((b) => (
                              <tr key={b.source_row_index}>
                                <th scope="row" className="bank-fx-bank">{b.institution_name}</th>
                                {RATE_FIELDS.map((f) => <td key={f} className="bank-fx-rate">{b[f]}</td>)}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </section>
                  <MarketAverage average={active.market_average} />
                </>
              )}

              <section aria-label="Source and rights">
                <div className="panel-heading"><span>SOURCE · RIGHTS</span><span className="muted">{bankFxRightsHeading(shown.rights_status)}</span></div>
                <p className="notice">{bankFxRightsText(shown.rights_status)}</p>
                <dl className="quote-grid bank-fx-provenance">
                  <div><dt>SOURCE</dt><dd><a href={shown.source_url} target="_blank" rel="noreferrer noopener">Bank of Zambia</a> <span className="bank-fx-url">{shown.source_url}</span></dd></div>
                  <div><dt>SOURCE TITLE</dt><dd className="bank-fx-url">{shown.source_title}</dd></div>
                  <div><dt>SOURCE RATES DATETIME (AS PUBLISHED)</dt><dd className="bank-fx-url">{shown.source_rates_datetime_raw}</dd></div>
                  <div><dt>RETRIEVED</dt><dd>{formatLusaka(shown.retrieved_at)}</dd></div>
                  <div><dt>NODE · REVISION</dt><dd>{shown.source_internal_nid} · r{shown.source_revision_id}</dd></div>
                </dl>
                <details className="bank-fx-details">
                  <summary>Technical provenance</summary>
                  <dl className="quote-grid bank-fx-provenance">
                    <div><dt>SOURCE UUID</dt><dd className="bank-fx-url">{shown.source_record_id}</dd></div>
                    <div><dt>INTERNAL NID</dt><dd>{shown.source_internal_nid}</dd></div>
                    <div><dt>REVISION ID</dt><dd>{shown.source_revision_id}</dd></div>
                    <div><dt>SOURCE CREATED</dt><dd className="bank-fx-url">{shown.source_created}</dd></div>
                    <div><dt>SOURCE CHANGED</dt><dd className="bank-fx-url">{shown.source_changed}</dd></div>
                    <div><dt>REVISION TIMESTAMP</dt><dd className="bank-fx-url">{shown.source_revision_timestamp}</dd></div>
                  </dl>
                </details>
              </section>
            </>
          )}
        </div>
      </article>
    );
  },
);

export default BankFxDetail;
