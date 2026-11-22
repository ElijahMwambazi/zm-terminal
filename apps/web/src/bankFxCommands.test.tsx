import { describe, expect, it } from "vitest";
import { COMMAND_NAMES, parseCommand } from "./commands";

describe("Bank FX commands", () => {
  it.each(["BANK FX", "bank fx", " BANK   FX", "FX BANKS", "fx banks"])("%j opens Bank FX", (input) => {
    expect(parseCommand(input)).toEqual({ ok: true, action: { type: "open-bank-fx" } });
  });
  it.each(["BANK", "BANKS", "FX", "BANK RATES", "BANKFX", "FX BANK", "BANK FX 2030-01-02"])("%j stays unknown", (input) => {
    expect(parseCommand(input).ok).toBe(false);
  });
  it("is listed in the suggestions", () => {
    expect(COMMAND_NAMES).toEqual(expect.arrayContaining(["BANK FX", "FX BANKS"]));
  });
});

const sources = import.meta.glob<string>(
  ["./api.ts", "./BankFxDetail.tsx", "./useBankFx.ts"],
  { query: "?raw", import: "default", eager: true },
);

describe("Bank FX frontend networking", () => {
  it("never hard-codes a BoZ URL, and only the API client calls fetch", () => {
    expect(Object.keys(sources)).toHaveLength(3);
    const bankFxApi = sources["./api.ts"].slice(sources["./api.ts"].indexOf("Bank FX: stored BoZ"));
    for (const [file, text] of Object.entries(sources)) expect(file === "./api.ts" ? bankFxApi : text, file).not.toMatch(/boz\.zm/i);
    expect(sources["./BankFxDetail.tsx"]).not.toMatch(/\bfetch\(/);
    expect(sources["./useBankFx.ts"]).not.toMatch(/\bfetch\(/);
  });
  it("derives no analytics and assigns no timezone to slot labels", () => {
    const detail = sources["./BankFxDetail.tsx"];
    expect(detail).not.toMatch(/toFixed|Number\(|parseFloat|Math\.(max|min)|\.sort\(|\.reduce\(/);
    expect(detail).not.toMatch(/CAT|UTC/);
  });
});
