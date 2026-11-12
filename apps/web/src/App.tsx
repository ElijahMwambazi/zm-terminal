import { useEffect, useMemo, useRef, useState } from "react";
import { getSystemStatus, type SystemStatus, type Tenor } from "./api";
import { COMMAND_NAMES, parseCommand } from "./commands";
import { formatDate, formatLusaka } from "./format";
import TbillDetail, { fallbackWarning, rejectedNote, tbillProblem } from "./TbillDetail";
import { useTreasuryBills } from "./useTreasuryBills";
import UsdZmwDetail, { QuoteBlock, quoteProblem } from "./UsdZmwDetail";
import { useUsdZmwQuote } from "./useUsdZmwQuote";

type MarketCard = {
  label: string;
  value: string;
  context: string;
};

const markets: MarketCard[] = [
  { label: "LASI", value: "—", context: "LuSE adapter" },
  { label: "BTC/USD", value: "—", context: "OpenBB / crypto adapter" },
  { label: "BTC/ZMW", value: "—", context: "Derived rate" },
  { label: "COPPER", value: "—", context: "Commodity adapter" },
];

function StatusDot({ ok }: { ok?: boolean }) {
  return <span className={`status-dot ${ok ? "status-dot--ok" : "status-dot--off"}`} />;
}

export default function App() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [command, setCommand] = useState("");
  const [commandError, setCommandError] = useState<string | null>(null);
  const [detail, setDetail] = useState<"usdzmw" | "tbill" | null>(null);
  const [tbillFocus, setTbillFocus] = useState<Tenor | null>(null);
  const { state: tbillState, refresh: refreshTbills } = useTreasuryBills();
  const { state: quoteState, refreshKey, refresh } = useUsdZmwQuote();
  const detailRef = useRef<HTMLElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const openUsdZmw = () => {
    setDetail("usdzmw");
    // Wait for the panel to mount before focusing it.
    requestAnimationFrame(() => detailRef.current?.focus());
  };

  const openTbill = (tenor: Tenor | null) => {
    setTbillFocus(tenor);
    setDetail("tbill");
    requestAnimationFrame(() => detailRef.current?.focus());
  };

  const runCommand = () => {
    const result = parseCommand(command);
    if (!result.ok) return setCommandError(result.error);
    setCommandError(null);
    setCommand("");
    if (result.action.type === "open-usdzmw") openUsdZmw();
    else openTbill(result.action.tenor);
  };

  useEffect(() => {
    getSystemStatus().then(setStatus).catch(() => setStatus(null));
  }, []);

  const tbill364 = tbillState.status === "ready" ? tbillState.data.tenors.find((t) => t.tenor === "364D") : undefined;

  const tbillSkipped = tbillState.status === "ready" ? tbillState.data.skipped_tenders : [];

  const now = useMemo(
    () =>
      new Intl.DateTimeFormat("en-ZM", {
        timeZone: "Africa/Lusaka",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }).format(new Date()),
    [],
  );

  return (
    <main className="terminal-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">PERSONAL MARKET INTELLIGENCE</p>
          <h1>ZM TERMINAL</h1>
        </div>
        <div className="clock">{now} CAT</div>
      </header>

      <section className="status-strip">
        <span><StatusDot ok={status?.api} /> API</span>
        <span><StatusDot ok={status?.database} /> POSTGRES</span>
        <span><StatusDot ok={status?.openbb} /> OPENBB</span>
        <span className="status-note">v0.1 — only USD/ZMW and Treasury Bills (Bank of Zambia) are live</span>
      </section>

      <section className="market-grid" aria-label="Market overview">
        <button type="button" className="market-card market-card--live" onClick={openUsdZmw}>
          <span className="market-label">USD/ZMW</span>
          {quoteState.status === "loading" && <strong>…</strong>}
          {quoteState.status === "error" && <strong>—</strong>}
          {quoteState.status === "ready" && <strong>{quoteState.quote.mid}</strong>}
          <small>
            {quoteState.status === "loading" && "Loading Bank of Zambia quote…"}
            {quoteState.status === "error" && quoteProblem(quoteState)}
            {quoteState.status === "ready" && (
              <>
                {quoteState.quote.buy} / {quoteState.quote.sell} · BoZ · {formatLusaka(quoteState.quote.observed_at)}
                {" · "}<span className={quoteState.quote.stale ? "text-stale" : "text-fresh"}>{quoteState.quote.stale ? "STALE" : "FRESH"}</span>
              </>
            )}
          </small>
        </button>
        <button type="button" className="market-card market-card--live" onClick={() => openTbill("364D")}>
          <span className="market-label">364D T-BILL</span>
          {tbill364 ? <strong>{tbill364.yield_rate}%</strong> : <strong>{tbillState.status === "loading" ? "…" : "—"}</strong>}
          <small>
            {tbillState.status === "loading" && "Loading Bank of Zambia auction…"}
            {tbillState.status === "error" && tbillProblem(tbillState)}
            {tbillState.status === "ready" && !tbill364 && "No recent 364D auction published"}
            {tbill364 && (
              <>
                Yield · BoZ · auction {formatDate(tbill364.auction_date)}
                {tbill364.stale && <> · <span className="text-stale">STALE</span></>}
              </>
            )}
            {tbill364 && fallbackWarning(tbill364) && (
              <><br /><span className="text-stale">{fallbackWarning(tbill364)}</span></>
            )}
            {tbill364 && !fallbackWarning(tbill364) && rejectedNote(tbillSkipped) && (
              <><br /><span className="muted">{rejectedNote(tbillSkipped)}</span></>
            )}
          </small>
        </button>
        {markets.map((market) => (
          <article className="market-card" key={market.label}>
            <span className="market-label">{market.label}</span>
            <strong>{market.value}</strong>
            <small>{market.context}</small>
          </article>
        ))}
      </section>

      <section className="workspace-grid">
        {detail === "usdzmw" ? (
          <UsdZmwDetail ref={detailRef} state={quoteState} refreshKey={refreshKey} onRefresh={refresh} />
        ) : detail === "tbill" ? (
          <TbillDetail ref={detailRef} state={tbillState} focus={tbillFocus} onRefresh={refreshTbills} />
        ) : (
          <article className="panel">
            <div className="panel-heading"><span>WORKSPACE</span></div>
            <p className="notice">Type <code>FX USDZMW</code> or <code>USDZMW</code> (Ctrl + K) for USD/ZMW, or <code>TBILL ZM</code> for Treasury Bills.</p>
          </article>
        )}

        <article className="panel">
          <div className="panel-heading">
            <span>WATCHLIST</span>
            <span className="muted">LOCAL</span>
          </div>
          <table>
            <tbody>
              <tr><td>CECZ</td><td>Copperbelt Energy</td><td>—</td></tr>
              <tr><td>ZNCO</td><td>Zanaco</td><td>—</td></tr>
              <tr><td>BTC</td><td>Bitcoin</td><td>—</td></tr>
              <tr><td>COPPER</td><td>Copper</td><td>—</td></tr>
            </tbody>
          </table>
        </article>
      </section>

      <section className="panel command-panel">
        <div className="panel-heading">
          <span>COMMAND PALETTE</span>
          <span className="muted">Ctrl + K</span>
        </div>
        <div className="command-suggestions">
          {COMMAND_NAMES.map((item) => <code key={item}>{item}</code>)}
        </div>
        <form onSubmit={(event) => { event.preventDefault(); runCommand(); }} className="command-line">
          <span>&gt;</span>
          <input
            aria-label="Terminal command"
            value={command}
            ref={inputRef}
            onChange={(event) => { setCommand(event.target.value.toUpperCase()); setCommandError(null); }}
            placeholder="TYPE A COMMAND"
            autoComplete="off"
          />
        </form>
        {commandError && <p className="notice notice--error" role="alert">{commandError}</p>}
      </section>
    </main>
  );
}
