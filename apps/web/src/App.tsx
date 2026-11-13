import { useEffect, useMemo, useRef, useState } from "react";
import { getSystemStatus, type AgricultureCommodity, type BondTenor, type FuelProduct, type SystemStatus, type Tenor } from "./api";
import { COMMAND_NAMES, parseCommand } from "./commands";
import FuelDetail, { archiveLags, perLitre } from "./FuelDetail";
import AgricultureDetail, { perKg } from "./AgricultureDetail";
import { formatDate, formatLusaka, formatMonth } from "./format";
import { useFuel } from "./useFuel";
import { useAgriculture } from "./useAgriculture";
import TbillDetail, { fallbackWarning, rejectedNote, tbillProblem } from "./TbillDetail";
import BondDetail, { bondFallbackWarning, bondProblem } from "./BondDetail";
import { useGovernmentBonds } from "./useGovernmentBonds";
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
  const [detail, setDetail] = useState<"usdzmw" | "tbill" | "bond" | "fuel" | "agriculture" | null>(null);
  const [fuelFocus, setFuelFocus] = useState<FuelProduct | null>(null);
  const { state: fuelState, refresh: refreshFuel } = useFuel();
  const [agricultureFocus, setAgricultureFocus] = useState<AgricultureCommodity | null>(null);
  const { state: agricultureState, refresh: refreshAgriculture } = useAgriculture();
  const [bondFocus, setBondFocus] = useState<BondTenor | null>(null);
  const { state: bondState, refresh: refreshBonds } = useGovernmentBonds();
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

  const openBond = (tenor: BondTenor | null) => {
    setBondFocus(tenor);
    setDetail("bond");
    requestAnimationFrame(() => detailRef.current?.focus());
  };

  const openFuel = (product: FuelProduct | null) => {
    setFuelFocus(product);
    setDetail("fuel");
    requestAnimationFrame(() => detailRef.current?.focus());
  };

  const openAgriculture = (commodity: AgricultureCommodity | null) => {
    setAgricultureFocus(commodity);
    setDetail("agriculture");
    requestAnimationFrame(() => detailRef.current?.focus());
  };

  const runCommand = () => {
    const result = parseCommand(command);
    if (!result.ok) return setCommandError(result.error);
    setCommandError(null);
    setCommand("");
    if (result.action.type === "open-usdzmw") openUsdZmw();
    else if (result.action.type === "open-tbill") openTbill(result.action.tenor);
    else if (result.action.type === "open-bond") openBond(result.action.tenor);
    else if (result.action.type === "open-fuel") openFuel(result.action.product);
    else openAgriculture(result.action.commodity);
  };

  useEffect(() => {
    getSystemStatus().then(setStatus).catch(() => setStatus(null));
  }, []);

  const tbill364 = tbillState.status === "ready" ? tbillState.data.tenors.find((t) => t.tenor === "364D") : undefined;

  const bond10 = bondState.status === "ready" ? bondState.data.tenors.find((t) => t.tenor === "10Y") : undefined;
  const petrol = fuelState.status === "ready" ? fuelState.data.prices.find((p) => p.product === "PETROL") : undefined;
  const maize = agricultureState.status === "ready" ? agricultureState.data.prices.find((p) => p.commodity === "MAIZE") : undefined;
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
        <span className="status-note">v0.1 — USD/ZMW, BoZ fixed income, ERB fuel, and Ministry agriculture averages</span>
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
        <button type="button" className="market-card market-card--live" onClick={() => openBond("10Y")}>
          <span className="market-label">10Y GRZ BOND</span>
          {bond10 ? <strong>{bond10.yield_rate}%</strong> : <strong>{bondState.status === "loading" ? "…" : "—"}</strong>}
          <small>
            {bondState.status === "loading" && "Loading Bank of Zambia auction…"}
            {bondState.status === "error" && bondProblem(bondState)}
            {bondState.status === "ready" && !bond10 && "No valid 10Y auction found"}
            {bond10 && <>Yield · BoZ · auction {formatDate(bond10.auction_date)}</>}
            {bond10 && bondFallbackWarning(bond10) && (
              <><br /><span className="text-stale">{bondFallbackWarning(bond10)}</span></>
            )}
          </small>
        </button>
        <button type="button" className="market-card market-card--live" onClick={() => openFuel("PETROL")}>
          <span className="market-label">PETROL ZM</span>
          {petrol ? <strong>{perLitre(petrol.price_zmw)}</strong> : <strong>{fuelState.status === "loading" ? "…" : "—"}</strong>}
          <small>
            {fuelState.status === "loading" && "Loading stored ERB prices…"}
            {fuelState.status === "error" && `${fuelState.message}. No price is shown.`}
            {fuelState.status === "ready" && !petrol && "No stored ERB petrol price"}
            {petrol && <>ERB · effective {formatMonth(petrol.effective_date)}</>}
            {petrol && archiveLags(petrol.effective_date) && (
              <><br /><span className="text-stale">Newest month in ERB archive</span></>
            )}
          </small>
        </button>
        <button type="button" className="market-card market-card--live" onClick={() => openAgriculture("MAIZE")}>
          <span className="market-label">MAIZE ZM</span>
          {maize ? <strong>{perKg(maize.source_price)}</strong> : <strong>{agricultureState.status === "loading" ? "…" : "—"}</strong>}
          <small>
            {agricultureState.status === "loading" && "Loading stored Ministry prices…"}
            {agricultureState.status === "error" && `${agricultureState.message}. No price is shown.`}
            {agricultureState.status === "ready" && !maize && "No stored Ministry maize average"}
            {maize && <>Ministry indicative avg · {formatMonth(maize.period_start)}</>}
            {maize && <><br /><span className="text-stale">Published period, not a live market quote</span></>}
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
        ) : detail === "fuel" ? (
          <FuelDetail ref={detailRef} state={fuelState} focus={fuelFocus} onRefresh={refreshFuel} />
        ) : detail === "agriculture" ? (
          <AgricultureDetail ref={detailRef} state={agricultureState} focus={agricultureFocus} onRefresh={refreshAgriculture} />
        ) : detail === "bond" ? (
          <BondDetail ref={detailRef} state={bondState} focus={bondFocus} onRefresh={refreshBonds} />
        ) : detail === "tbill" ? (
          <TbillDetail ref={detailRef} state={tbillState} focus={tbillFocus} onRefresh={refreshTbills} />
        ) : (
          <article className="panel">
            <div className="panel-heading"><span>WORKSPACE</span></div>
            <p className="notice">Type <code>FX USDZMW</code> or <code>USDZMW</code> (Ctrl + K) for USD/ZMW, <code>TBILL ZM</code> for Treasury Bills, <code>BOND ZM</code> for Government Bonds, <code>FUEL ZM</code> for fuel prices, or <code>AGRI ZM</code> for Ministry agriculture averages.</p>
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
