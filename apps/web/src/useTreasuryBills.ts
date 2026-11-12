import { useCallback, useEffect, useState } from "react";
import { ApiError, getTreasuryBills, type TreasuryBillsLatest } from "./api";

export type TbillState =
  | { status: "loading" }
  | { status: "ready"; data: TreasuryBillsLatest }
  | { status: "error"; kind: "upstream" | "api" };

/** Loads once on mount; `refresh` is an explicit user action (one BoZ request each). */
export function useTreasuryBills() {
  const [state, setState] = useState<TbillState>({ status: "loading" });
  const load = useCallback(() => {
    setState({ status: "loading" });
    getTreasuryBills()
      .then((data) => setState({ status: "ready", data }))
      .catch((error: unknown) =>
        setState({ status: "error", kind: error instanceof ApiError ? error.kind : "api" }));
  }, []);
  useEffect(load, [load]);
  return { state, refresh: load };
}
