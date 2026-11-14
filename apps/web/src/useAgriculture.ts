import { useCallback, useEffect, useState } from "react";
import { ApiError, getAgricultureLatest, type AgricultureLatest } from "./api";

export type AgricultureState =
  | { status: "loading" }
  | { status: "ready"; data: AgricultureLatest }
  | { status: "error"; message: string };

export function useAgriculture() {
  const [state, setState] = useState<AgricultureState>({ status: "loading" });
  const load = useCallback(() => {
    setState({ status: "loading" });
    getAgricultureLatest()
      .then((data) => setState({ status: "ready", data }))
      .catch((error: unknown) =>
        setState({
          status: "error",
          message: error instanceof ApiError && error.message.includes("(503)")
            ? "Stored agriculture prices are unavailable (database)"
            : "ZM Terminal API is unavailable",
        }),
      );
  }, []);
  useEffect(load, [load]);
  return { state, refresh: load };
}
