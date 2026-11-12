export type CommandResult =
  | { ok: true; action: { type: "open-usdzmw" } }
  | { ok: false; error: string };

/** Normalized command text -> action. Add new commands here. */
const COMMANDS: Record<string, CommandResult & { ok: true }> = {
  "FX USDZMW": { ok: true, action: { type: "open-usdzmw" } },
  USDZMW: { ok: true, action: { type: "open-usdzmw" } },
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
