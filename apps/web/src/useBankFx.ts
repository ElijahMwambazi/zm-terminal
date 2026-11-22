import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getBankFxLatest, type BankFxLatest } from "./api";

export type BankFxState =
  | { status: "loading" }
  | { status: "ready"; data: BankFxLatest; refreshing?: boolean }
  | { status: "error"; message: string };

/** Reads the latest STORED commercial bank FX snapshot from the ZM Terminal API. This hook never contacts BoZ; `refresh` only re-reads
 * PostgreSQL through the API. No polling. Newest request wins (a response is applied only if it is still the newest and the component is
 * mounted), and a refresh keeps the previous data visible instead of blanking it. */
export function useBankFx() {
  const [state, setState] = useState<BankFxState>({ status: "loading" });
  const generation = useRef(0);
  const load = useCallback(() => {
    const id = ++generation.current;
    setState((prev) => (prev.status === "ready" ? { ...prev, refreshing: true } : { status: "loading" }));
    getBankFxLatest()
      .then((data) => { if (id === generation.current) setState({ status: "ready", data }); })
      .catch((error: unknown) => {
        if (id === generation.current) setState({ status: "error", message: error instanceof ApiError ? error.message : "ZM Terminal API is unavailable" });
      });
  }, []);
  useEffect(() => {
    load();
    return () => { generation.current += 1; }; // unmount invalidates anything in flight
  }, [load]);
  return { state, refresh: load };
}
