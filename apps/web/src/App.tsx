import { useEffect, useMemo, useState } from "react";
import { getSystemStatus, type SystemStatus } from "./api";

type MarketCard = {
  label: string;
  value: string;
  context: string;
};

const markets: MarketCard[] = [
  { label: "USD/ZMW", value: "—", context: "Bank of Zambia adapter" },
  { label: "LASI", value: "—", context: "LuSE adapter" },
  { label: "BTC/USD", value: "—", context: "OpenBB / crypto adapter" },
  { label: "BTC/ZMW", value: "—", context: "Derived rate" },
  { label: "COPPER", value: "—", context: "Commodity adapter" },
  { label: "364D T-BILL", value: "—", context: "BoZ securities adapter" },
];

const commands = [
  "FX USDZMW",
  "LU CECZ",
  "BTC",
  "BTCZMW",
  "COPPER",
  "TBILL ZM",
  "MACRO ZM",
  "WATCH",
];

function StatusDot({ ok }: { ok?: boolean }) {
  return <span className={`status-dot ${ok ? "status-dot--ok" : "status-dot--off"}`} />;
}

export default function App() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [command, setCommand] = useState("");

  useEffect(() => {
    getSystemStatus().then(setStatus).catch(() => setStatus(null));
  }, []);

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
        <span className="status-note">v0.1 scaffold — live market adapters are intentionally not wired yet</span>
      </section>

      <section className="market-grid" aria-label="Market overview">
        {markets.map((market) => (
          <article className="market-card" key={market.label}>
            <span className="market-label">{market.label}</span>
            <strong>{market.value}</strong>
            <small>{market.context}</small>
          </article>
        ))}
      </section>

      <section className="workspace-grid">
        <article className="panel chart-panel">
          <div className="panel-heading">
            <span>USD/ZMW</span>
            <span className="muted">1Y</span>
          </div>
          <div className="chart-placeholder">
            <div className="chart-line" />
            <p>Historical chart will appear when the BoZ adapter is connected.</p>
          </div>
        </article>

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
          <span className="muted">Ctrl + K planned</span>
        </div>
        <div className="command-suggestions">
          {commands.map((item) => <code key={item}>{item}</code>)}
        </div>
        <form onSubmit={(event) => event.preventDefault()} className="command-line">
          <span>&gt;</span>
          <input
            aria-label="Terminal command"
            value={command}
            onChange={(event) => setCommand(event.target.value.toUpperCase())}
            placeholder="TYPE A COMMAND"
            autoComplete="off"
          />
        </form>
      </section>
    </main>
  );
}
