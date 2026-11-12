import type { Tenor } from "./api";

export type CommandResult =
  | { ok: true; action: { type: "open-usdzmw" } | { type: "open-tbill"; tenor: Tenor | null } }
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
