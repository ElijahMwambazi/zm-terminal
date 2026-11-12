import { useCallback, useEffect, useState } from "react";
import { ApiError, getGovernmentBonds, type GovernmentBondsLatest } from "./api";

export type BondState =
  | { status: "loading" }
  | { status: "ready"; data: GovernmentBondsLatest }
  | { status: "error"; kind: "upstream" | "api" };

/** Loads once on mount; `refresh` is an explicit user action (one BoZ request each). */
export function useGovernmentBonds() {
  const [state, setState] = useState<BondState>({ status: "loading" });
  const load = useCallback(() => {
    setState({ status: "loading" });
    getGovernmentBonds()
      .then((data) => setState({ status: "ready", data }))
      .catch((error: unknown) =>
        setState({ status: "error", kind: error instanceof ApiError ? error.kind : "api" }));
  }, []);
  useEffect(load, [load]);
  return { state, refresh: load };
}
