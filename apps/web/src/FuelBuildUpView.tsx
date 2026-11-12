import { useEffect, useState } from "react";
import { ApiError, FUEL_LABELS, getFuelBuildUp, type BuildUpComponent, type BuildUpType, type FuelBuildUp, type FuelPrice, type FuelProduct } from "./api";
import { formatMonth } from "./format";

/** Thousands separators on the printed decimal string (no float round trip). */
export function groupDigits(amount: string): string {
  const negative = amount.startsWith("-");
  const [whole, fraction] = amount.replace("-", "").split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${negative ? "−" : ""}${grouped}${fraction !== undefined ? `.${fraction}` : ""}`;
}

export function formatComponent(c: BuildUpComponent): string {
  if (c.amount === null) return "—";
  const value = groupDigits(c.amount);
  return c.currency === "ZMW" ? `K${value}` : c.currency === "USD" ? `$${value}` : value;
}

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: FuelBuildUp | null };

// Products ERB publishes a build-up for (pump: the three pump-price products; Jet A-1 has a wholesale PDF only).
const PUMP_PRODUCTS: FuelProduct[] = ["PETROL", "DIESEL", "KEROSENE"];
const WHOLESALE_PRODUCTS: FuelProduct[] = ["PETROL", "DIESEL", "KEROSENE", "JET_A1"];
const KIND_LABEL: Record<BuildUpType, string> = { pump: "pump", wholesale: "wholesale" };

/** One ERB build-up exactly as published, in ERB's order. Nothing is added up or recomputed here. */
export default function FuelBuildUpView(
  { product, type, retail, reloadKey }: { product: FuelProduct; type: BuildUpType; retail: FuelPrice | undefined; reloadKey: unknown },
) {
  const published = type === "pump" ? PUMP_PRODUCTS : WHOLESALE_PRODUCTS;
  const [state, setState] = useState<State>({ status: "loading" });
  useEffect(() => {
    if (!published.includes(product)) { setState({ status: "ready", data: null }); return; }  // ERB publishes none: no request
    let cancelled = false;
    setState({ status: "loading" });
    getFuelBuildUp(product, type)
      .then((data) => !cancelled && setState({ status: "ready", data }))
      .catch((error: unknown) =>
        !cancelled && setState({ status: "error", message: error instanceof ApiError ? error.message : "Build-up unavailable" }));
    return () => { cancelled = true; };
  }, [product, type, reloadKey]);

  if (state.status === "loading") return <p className="notice">Loading stored ERB build-up…</p>;
  if (state.status === "error") return <p className="notice notice--error" role="alert">{state.message}. No build-up is shown.</p>;
  const data = state.data;
  if (!data) {
    return published.includes(product)
      ? <p className="notice">No {KIND_LABEL[type]} build-up is stored for {FUEL_LABELS[product]}. Import with <code>python -m app.jobs.backfill_fuel_buildups --from 2025-09</code>.</p>
      : <p className="notice">ERB does not publish a {KIND_LABEL[type]} build-up for {FUEL_LABELS[product]}.</p>;
  }
  const sameMonth = retail !== undefined && retail.effective_date === data.effective_date;
  return (
    <div className="buildup" aria-label={`${FUEL_LABELS[product]} ${type} build-up`}>
      <p className="notice"><strong>{FUEL_LABELS[product]} · {formatMonth(data.effective_date).toUpperCase()}</strong> · {type === "pump" ? "pump price build-up" : "wholesale price build-up"} as published by ERB</p>
      <div className="table-scroll">
        <table>
          <thead><tr><th>COMPONENT</th><th>AMOUNT</th><th>UNIT</th></tr></thead>
          <tbody>
            {data.components.map((c, index) => (
              <tr key={`${c.code}-${c.unit ?? ""}-${index}`}>
                <td>{c.label}{c.note && <div className="muted">{c.note}</div>}</td>
                <td>{formatComponent(c)}</td>
                <td>{c.unit ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {type === "pump" && sameMonth && retail && (
        <p className="notice">Official retail (ERB press statement / archive): <strong>K{retail.price_zmw}/L</strong></p>
      )}
      {type === "pump" && !sameMonth && (
        <p className="notice text-stale">No official K/litre price is stored for {formatMonth(data.effective_date)}: ERB&apos;s press statement for the month could not be read and validated. The uniform pump price above is ERB&apos;s build-up value in ZMW per cubic metre.</p>
      )}
      <p className="notice">Rows are shown exactly as ERB published them, in ERB&apos;s order. They are not recomputed, and the table does not claim every row adds to the next. A dash means ERB did not publish a value.</p>
      <ul className="provenance">
        <li><span>Source</span><a href={data.source_url} target="_blank" rel="noreferrer">Energy Regulation Board Zambia</a> ({type} price build-up PDF)</li>
      </ul>
    </div>
  );
}
