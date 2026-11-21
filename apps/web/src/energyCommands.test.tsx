import { describe, expect, it } from "vitest";
import { COMMAND_NAMES, parseCommand } from "./commands";

describe("Energy commands", () => {
  it.each(["POWER ZM", "power zm", " ENERGY   ZM", "GENERATION ZM"])("%j opens Energy", (input) => {
    expect(parseCommand(input)).toEqual({ ok: true, action: { type: "open-energy" } });
  });
  it.each(["POWER", "ENERGY", "POWERZM", "POWER ZAMBIA", "GENERATION"])("%j stays unknown", (input) => {
    expect(parseCommand(input).ok).toBe(false);
  });
  it("is listed in the suggestions", () => {
    expect(COMMAND_NAMES).toEqual(expect.arrayContaining(["POWER ZM", "ENERGY ZM", "GENERATION ZM"]));
  });
});

const sources = import.meta.glob<string>(
  ["./api.ts", "./EnergyGenerationDetail.tsx", "./EnergyAnnualChart.tsx", "./useEnergyGeneration.ts"],
  { query: "?raw", import: "default", eager: true },
);

describe("Energy frontend networking", () => {
  it("never hard-codes an ERB URL or fetches anything but the ZM Terminal API", () => {
    expect(Object.keys(sources)).toHaveLength(4);
    for (const [file, text] of Object.entries(sources)) expect(text, file).not.toMatch(/erb\.org\.zm/i);
    expect(sources["./EnergyGenerationDetail.tsx"]).not.toMatch(/\bfetch\(/);
  });
});
