import { afterEach, describe, expect, it, vi } from "vitest";
import { getBankFxDate, getBankFxLatest, parseBankFxLatest, parseBankFxSnapshot } from "./api";
import { row, slot, snapshot } from "./bankFxFixtures";

afterEach(() => vi.unstubAllGlobals());
const withSlot = (s: unknown) => snapshot({ slots: [s] });
const stub = (status: number, body: unknown = {}) => {
  const fn = vi.fn(() => Promise.resolve(new Response(typeof body === "string" ? body : JSON.stringify(body), { status })));
  vi.stubGlobal("fetch", fn);
  return fn;
};

describe("Bank FX response validation", () => {
  it("accepts a valid snapshot and a null latest", () => {
    expect(parseBankFxSnapshot(snapshot()).slots).toHaveLength(3);
    expect(parseBankFxLatest({ snapshot: null })).toEqual({ snapshot: null });
    expect(parseBankFxLatest({ snapshot: snapshot() }).snapshot?.observation_date).toBe("2030-01-02");
  });
  it("accepts any bank count, including none", () => {
    expect(parseBankFxSnapshot(withSlot({ ...slot("09:30", 10), banks: [] })).slots[0].banks).toEqual([]);
    const many = Array.from({ length: 31 }, (_, i) => row(i + 3, `BANK ${i}`, 10));
    expect(parseBankFxSnapshot(withSlot({ ...slot("09:30", 10, false), banks: many })).slots[0].banks).toHaveLength(31);
  });
  it("keeps every rate string exactly as published", () => {
    const s = parseBankFxSnapshot(withSlot({ ...slot("09:30", 10), banks: [row(3, "A", 9, { interbank_buy: "9.9", interbank_sell: "9.90", retail_buy: "9.900", retail_sell: "9.9000" })] }));
    const b = s.slots[0].banks[0];
    expect([b.interbank_buy, b.interbank_sell, b.retail_buy, b.retail_sell]).toEqual(["9.9", "9.90", "9.900", "9.9000"]);
  });
  it.each([9.9, 10, null, true, ["9.9"]])("rejects the non-string rate %j", (v) => {
    expect(() => parseBankFxSnapshot(withSlot({ ...slot("09:30", 10), banks: [row(3, "A", 9, { retail_buy: v as never })] }))).toThrow();
  });
  it.each(["0.0", "0.0000", "0.00", "-9.9", "+9.9", "9.9e1", "9.9E1", " 9.9", "9.9 ", "9.99999", "9", ".9", "9.", "9,9", "1_0.5", "", "NaN", "9.9\n"])("rejects the rate %j", (v) => {
    expect(() => parseBankFxSnapshot(withSlot({ ...slot("09:30", 10), banks: [row(3, "A", 9, { interbank_sell: v })] }))).toThrow();
  });
  it("rejects an unknown, duplicate or out-of-order slot", () => {
    expect(() => parseBankFxSnapshot(withSlot(slot("10:00" as never, 10)))).toThrow();
    expect(() => parseBankFxSnapshot(snapshot({ slots: [slot("09:30", 10), slot("09:30", 20)] }))).toThrow();
    expect(() => parseBankFxSnapshot(snapshot({ slots: [slot("12:30", 10), slot("09:30", 20)] }))).toThrow();
    expect(() => parseBankFxSnapshot(snapshot({ slots: [] }))).toThrow();
  });
  it("rejects bank rows out of order, duplicated or sharing an identity", () => {
    const bad = (banks: unknown[]) => expect(() => parseBankFxSnapshot(withSlot({ ...slot("09:30", 10, false), banks }))).toThrow();
    bad([row(4, "A", 9), row(3, "B", 9)]);
    bad([row(3, "A", 9), row(3, "B", 9)]);
    bad([row(3, "A", 9), row(4, "A", 9)]);
  });
  it("rejects a market average inside banks or duplicating a bank row", () => {
    expect(() => parseBankFxSnapshot(withSlot({ ...slot("09:30", 10, false), banks: [row(3, "MARKET AVERAGE", 9)] }))).toThrow();
    expect(() => parseBankFxSnapshot(withSlot({ ...slot("09:30", 10, false), banks: [row(3, "Market   Average", 9)] }))).toThrow();
    expect(() => parseBankFxSnapshot(withSlot({ ...slot("09:30", 10), market_average: row(3, "MARKET AVERAGE", 9) }))).toThrow();
  });
  it("accepts a null market average", () => {
    expect(parseBankFxSnapshot(withSlot(slot("09:30", 10, false))).slots[0].market_average).toBeNull();
  });
  it("requires the retail label to match the layout", () => {
    expect(() => parseBankFxSnapshot(snapshot({}, ["09:30"], "v2"))).not.toThrow();
    expect(parseBankFxSnapshot(snapshot({}, ["09:30"], "v1")).slots[0].retail_source_label).toBe("Retail Rates");
    expect(() => parseBankFxSnapshot(snapshot({ slots: [slot("09:30", 10, true, "v1")] }))).toThrow(); // v2 layout, v1 label
    expect(() => parseBankFxSnapshot({ ...snapshot({}, ["09:30"], "v1"), table_layout: "v2" })).toThrow();
    expect(() => parseBankFxSnapshot(snapshot({ table_layout: "v3" }))).toThrow();
  });
  it.each(["2030-02-30", "2030-1-2", "20300102", "2030-13-01", "", "02 Jan 2030", null, 20300102])("rejects the observation date %j", (v) => {
    expect(() => parseBankFxSnapshot(snapshot({ observation_date: v }))).toThrow();
  });
  it.each([
    ["source_record_id", ""], ["source_title", ""], ["source_url", ""], ["source_url", "javascript:alert(1)"], ["source_url", "not a url"],
    ["source_revision_id", 0], ["source_revision_id", 1.5], ["source_internal_nid", -1], ["source_internal_nid", "11"],
    ["rights_status", ""], ["rights_status", null], ["source_rates_datetime_raw", ""], ["retrieved_at", "yesterday"], ["source_created", 5],
  ])("rejects malformed provenance %s=%j", (key, value) => {
    expect(() => parseBankFxSnapshot(snapshot({ [key]: value }))).toThrow();
  });
  it("rejects a bad source row index and empty names", () => {
    expect(() => parseBankFxSnapshot(withSlot({ ...slot("09:30", 10, false), banks: [row(0, "A", 9)] }))).toThrow();
    expect(() => parseBankFxSnapshot(withSlot({ ...slot("09:30", 10, false), banks: [row(3, "", 9)] }))).toThrow();
    expect(() => parseBankFxLatest({})).toThrow();
    expect(() => parseBankFxLatest([])).toThrow();
  });
});

describe("Bank FX API client", () => {
  it("reads only the stored latest endpoint", async () => {
    const f = stub(200, { snapshot: snapshot() });
    expect((await getBankFxLatest()).snapshot?.slots).toHaveLength(3);
    expect(f.mock.calls).toHaveLength(1);
    expect(String((f.mock.calls as unknown[][])[0][0])).toMatch(/\/api\/v1\/fx\/banks\/latest$/);
  });
  it("encodes the exact date and reads only the history endpoint", async () => {
    const f = stub(200, snapshot());
    expect((await getBankFxDate("2030-01-02"))?.observation_date).toBe("2030-01-02");
    expect(String((f.mock.calls as unknown[][])[0][0])).toMatch(/\/api\/v1\/fx\/banks\/history\?date=2030-01-02$/);
  });
  it("returns null for an exact-date 404 and never another date", async () => {
    const f = stub(404, { detail: "secret server detail" });
    expect(await getBankFxDate("2030-01-02")).toBeNull();
    expect(f.mock.calls).toHaveLength(1);
  });
  it("rejects a snapshot for a different date than requested", async () => {
    stub(200, snapshot({ observation_date: "2030-01-03" }));
    await expect(getBankFxDate("2030-01-02")).rejects.toThrow("Unexpected Bank FX response");
  });
  it("maps 503 to the safe stored-data message without leaking server text", async () => {
    stub(503, { detail: "postgresql://user:pw@db/zm_terminal exploded" });
    for (const call of [() => getBankFxLatest(), () => getBankFxDate("2030-01-02")]) {
      const error = await call().catch((e: Error) => e);
      expect((error as Error).message).toBe("Stored commercial bank FX data is unavailable (database)");
    }
  });
  it("maps 422 to a safe invalid-date error", async () => {
    stub(422, { detail: [{ msg: "secret validation text" }] });
    const error = await getBankFxDate("2030-02-30").catch((e: Error) => e);
    expect((error as Error).message).toBe("Invalid date for the stored Bank FX lookup");
  });
  it("fails the whole response closed on a malformed 200 and never leaks its body", async () => {
    stub(200, { snapshot: snapshot({ slots: [slot("09:30", 10), { ...slot("12:30", 20), banks: [row(3, "A", 9, { retail_buy: 9.9 as never })] }] }) });
    const error = await getBankFxLatest().catch((e: Error) => e);
    expect((error as Error).message).toBe("Unexpected Bank FX response");
    stub(200, "<html>not json secret</html>");
    expect(((await getBankFxLatest().catch((e: Error) => e)) as Error).message).toBe("Unexpected Bank FX response");
  });
  it("does not expose raw text for other failures or a network error", async () => {
    stub(500, { detail: "stack trace secret" });
    expect(((await getBankFxLatest().catch((e: Error) => e)) as Error).message).toBe("ZM Terminal API error (500)");
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("connect ECONNREFUSED secret-host"))));
    expect(((await getBankFxLatest().catch((e: Error) => e)) as Error).message).toBe("ZM Terminal API is unreachable");
  });
});
