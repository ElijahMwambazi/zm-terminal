import { useEffect, useState } from "react";
import { ApiError, type AuctionPoint } from "./api";
import AuctionChart from "./AuctionChart";
import { formatDate } from "./format";

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; points: AuctionPoint[] };

type Props<T extends string, R extends string> = {
  title: string;
  tenors: readonly T[];
  ranges: readonly R[];
  initialRange: R;
  focus: T | null;
  fallbackTenor: T;
  load: (tenor: T, range: R) => Promise<AuctionPoint[]>;
  /** Changes when the live view reloads (a successful live read is persisted), so history re-reads. */
  reloadKey: unknown;
};

/** Stored auction history for one tenor: yield per real auction. Never calls BoZ. */
export default function AuctionHistory<T extends string, R extends string>(props: Props<T, R>) {
  const { title, tenors, ranges, initialRange, focus, fallbackTenor, load, reloadKey } = props;
  const [tenor, setTenor] = useState<T>(focus ?? fallbackTenor);
  const [range, setRange] = useState<R>(initialRange);
  const [state, setState] = useState<State>({ status: "loading" });

  useEffect(() => { if (focus) setTenor(focus); }, [focus]);

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    load(tenor, range)
      .then((points) => !cancelled && setState({ status: "ready", points }))
      .catch((error: unknown) =>
        !cancelled && setState({ status: "error", message: error instanceof ApiError ? error.message : "History unavailable" }));
    return () => { cancelled = true; };
  }, [tenor, range, reloadKey, load]);

  return (
    <section className="history-section" aria-label={`${title} history`}>
      <div className="panel-heading"><span>{title} · YIELD AT EACH AUCTION</span></div>
      <div className="range-row" role="group" aria-label="History tenor">
        {tenors.map((item) => (
          <button key={item} type="button" className={item === tenor ? "range range--active" : "range"}
            aria-pressed={item === tenor} onClick={() => setTenor(item)}>{item}</button>
        ))}
      </div>
      <div className="range-row" role="group" aria-label="History range">
        {ranges.map((item) => (
          <button key={item} type="button" className={item === range ? "range range--active" : "range"}
            aria-pressed={item === range} onClick={() => setRange(item)}>{item}</button>
        ))}
      </div>
      {state.status === "loading" && <p className="notice">Loading stored history…</p>}
      {state.status === "error" && <p className="notice notice--error" role="alert">{state.message}. No history is shown.</p>}
      {state.status === "ready" && state.points.length === 0 && (
        <p className="notice">No stored {tenor} auctions in this range. Import with the backfill job or retrieve live data; missing auctions are never filled in.</p>
      )}
      {state.status === "ready" && state.points.length === 1 && (
        <p className="notice">Only one stored {tenor} auction in this range: {state.points[0].yield_rate}% on {formatDate(state.points[0].auction_date)} ({state.points[0].tender_number}).</p>
      )}
      {state.status === "ready" && state.points.length > 1 && (
        <>
          <AuctionChart points={state.points} label={`${tenor} auction yield history`} />
          <p className="notice">{state.points.length} stored auctions, {formatDate(state.points[0].auction_date)} to {formatDate(state.points[state.points.length - 1].auction_date)}. Each marker is one auction; there is no data between markers.</p>
        </>
      )}
    </section>
  );
}
