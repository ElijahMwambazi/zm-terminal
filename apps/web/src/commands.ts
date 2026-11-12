import type { BondTenor, FuelProduct, Tenor } from "./api";

export type CommandResult =
  | { ok: true; action: { type: "open-usdzmw" } | { type: "open-tbill"; tenor: Tenor | null }
    | { type: "open-bond"; tenor: BondTenor | null }
    | { type: "open-fuel"; product: FuelProduct | null } }
  | { ok: false; error: string };

/** Normalized command text -> action. Add new commands here. */
const COMMANDS: Record<string, CommandResult & { ok: true }> = {
  "FX USDZMW": { ok: true, action: { type: "open-usdzmw" } },
  USDZMW: { ok: true, action: { type: "open-usdzmw" } },
  "TBILL ZM": { ok: true, action: { type: "open-tbill", tenor: null } },
  "TBILL 91D": { ok: true, action: { type: "open-tbill", tenor: "91D" } },
  "TBILL 182D": { ok: true, action: { type: "open-tbill", tenor: "182D" } },
  "TBILL 273D": { ok: true, action: { type: "open-tbill", tenor: "273D" } },
  "TBILL 364D": { ok: true, action: { type: "open-tbill", tenor: "364D" } },
  "FUEL ZM": { ok: true, action: { type: "open-fuel", product: null } },
  "FUEL PETROL": { ok: true, action: { type: "open-fuel", product: "PETROL" } },
  "FUEL DIESEL": { ok: true, action: { type: "open-fuel", product: "DIESEL" } },
  "FUEL KEROSENE": { ok: true, action: { type: "open-fuel", product: "KEROSENE" } },
  "FUEL JET": { ok: true, action: { type: "open-fuel", product: "JET_A1" } },
  "FUEL LSGO": { ok: true, action: { type: "open-fuel", product: "LSGO" } },
  "BOND ZM": { ok: true, action: { type: "open-bond", tenor: null } },
  "BOND 2Y": { ok: true, action: { type: "open-bond", tenor: "2Y" } },
  "BOND 3Y": { ok: true, action: { type: "open-bond", tenor: "3Y" } },
  "BOND 5Y": { ok: true, action: { type: "open-bond", tenor: "5Y" } },
  "BOND 7Y": { ok: true, action: { type: "open-bond", tenor: "7Y" } },
  "BOND 10Y": { ok: true, action: { type: "open-bond", tenor: "10Y" } },
  "BOND 15Y": { ok: true, action: { type: "open-bond", tenor: "15Y" } },
};

export const COMMAND_NAMES = Object.keys(COMMANDS);

export function parseCommand(input: string): CommandResult {
  const normalized = input.trim().replace(/\s+/g, " ").toUpperCase();
  if (!normalized) return { ok: false, error: "Enter a command." };
  return (
    COMMANDS[normalized] ?? {
      ok: false,
      error: `Unknown command "${normalized}". Available: ${COMMAND_NAMES.join(", ")}`,
    }
  );
}
