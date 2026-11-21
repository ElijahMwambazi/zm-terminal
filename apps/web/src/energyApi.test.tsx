import { afterEach, describe, expect, it, vi } from "vitest";
import { getEnergyStationHistory, parseEnergyGroupHistory, parseEnergyHistory, parseEnergyLatest } from "./api";
import { ghistory, gpoint, history, hpoint, snapshot, station, total } from "./energyFixtures";

afterEach(() => vi.unstubAllGlobals());

describe("Energy response validation", () => {
  it("accepts a valid snapshot and keeps decimal strings exactly", () => {
    const out = parseEnergyLatest({ snapshot: snapshot() }).snapshot!;
    expect(out.stations[0].generation_gwh).toBe("393.40");
    expect(out.stations[1].generation_gwh).toBe("0.00");
    expect(out.group_totals).toHaveLength(4);
  });
  it("accepts a null snapshot", () => expect(parseEnergyLatest({ snapshot: null })).toEqual({ snapshot: null }));
  it("accepts a reported zero", () => {
    expect(() => parseEnergyLatest({ snapshot: snapshot({ stations: [station("zesco_diesel", "Z", "reported", "0.00")] }) })).not.toThrow();
  });
  it("requires not_reported to carry null value and precision", () => {
    const s = station("zesco_diesel", "Z", "not_reported", null);
    expect(() => parseEnergyLatest({ snapshot: snapshot({ stations: [{ ...s, generation_gwh: "1.00" }] }) })).toThrow();
    expect(() => parseEnergyLatest({ snapshot: snapshot({ stations: [{ ...s, published_precision: 2 }] }) })).toThrow();
    expect(() => parseEnergyLatest({ snapshot: snapshot({ stations: [{ ...station("zesco_diesel", "Z", "reported", "1.00"), published_precision: null }] }) })).toThrow();
  });
  it.each(["3.9E+2", "NaN", "Infinity", "-1.00", "abc", "1e3", ""])("rejects decimal %j", (v) => {
    expect(() => parseEnergyLatest({ snapshot: snapshot({ stations: [station("zesco_diesel", "Z", "reported", v)] }) })).toThrow();
  });
  it("rejects a non-string decimal", () => {
    expect(() => parseEnergyLatest({ snapshot: snapshot({ group_totals: [{ ...total("zesco_diesel", "1.00"), total_gwh: 1 }] }) })).toThrow();
  });
  it("rejects an unknown group", () => {
    expect(() => parseEnergyLatest({ snapshot: snapshot({ stations: [station("nuclear", "Z", "reported", "1.00")] }) })).toThrow();
  });
  it("rejects duplicate station identities but allows the same name in another group", () => {
    const a = station("zesco_diesel", "Z", "reported", "1.00");
    expect(() => parseEnergyLatest({ snapshot: snapshot({ stations: [a, a] }) })).toThrow();
    expect(() => parseEnergyLatest({ snapshot: snapshot({ stations: [a, station("zesco_mini_hydro", "Z", "reported", "1.00")] }) })).not.toThrow();
  });
  it("rejects duplicate group totals", () => {
    expect(() => parseEnergyLatest({ snapshot: snapshot({ group_totals: [total("zesco_diesel", "1.00"), total("zesco_diesel", "2.00")] }) })).toThrow();
  });
  it("rejects observation years that differ from the snapshot year", () => {
    expect(() => parseEnergyLatest({ snapshot: snapshot({ stations: [station("zesco_diesel", "Z", "reported", "1.00", 2029)] }) })).toThrow();
    expect(() => parseEnergyLatest({ snapshot: snapshot({ group_totals: [total("zesco_diesel", "1.00", 2029)] }) })).toThrow();
  });
  it("rejects bad pages and years", () => {
    expect(() => parseEnergyLatest({ snapshot: snapshot({ stations: [{ ...station("zesco_diesel", "Z", "reported", "1.00"), source_pdf_page: 0 }] }) })).toThrow();
    expect(() => parseEnergyLatest({ snapshot: snapshot({ observation_year: 2030.5 }) })).toThrow();
  });
  it("validates history: count and ordering", () => {
    expect(parseEnergyHistory(history("zesco_diesel", "Z")).count).toBe(3);
    expect(() => parseEnergyHistory({ ...history("zesco_diesel", "Z"), count: 2 })).toThrow();
    expect(() => parseEnergyHistory(history("zesco_diesel", "Z", [hpoint(2030, "1.00"), hpoint(2029, "1.00")]))).toThrow();
    expect(() => parseEnergyHistory(history("zesco_diesel", "Z", [hpoint(2030, "1.00"), hpoint(2030, "1.00")]))).toThrow();
  });
  it("validates group history", () => {
    expect(parseEnergyGroupHistory(ghistory("zesco_diesel")).count).toBe(2);
    expect(() => parseEnergyGroupHistory({ ...ghistory("zesco_diesel"), count: 5 })).toThrow();
    expect(() => parseEnergyGroupHistory(ghistory("zesco_diesel", [gpoint("zesco_diesel", 2031, "1.00"), gpoint("zesco_diesel", 2030, "1.00")]))).toThrow();
    expect(() => parseEnergyGroupHistory(ghistory("zesco_diesel"), "zesco_mini_hydro")).toThrow();
  });
});

describe("Energy API client", () => {
  it("URL-encodes the exact station identity and only calls the ZM Terminal API", async () => {
    const fetchMock = vi.fn((_url: string) => Promise.resolve(new Response(JSON.stringify(history("zesco_diesel", "A & B/ç")))));
    vi.stubGlobal("fetch", fetchMock);
    await getEnergyStationHistory("zesco_diesel", "A & B/ç");
    const url = new URL(String(fetchMock.mock.calls[0][0]));
    expect(url.pathname).toBe("/api/v1/energy/generation/history");
    expect(url.searchParams.get("station_name")).toBe("A & B/ç");
    expect(url.searchParams.get("station_group")).toBe("zesco_diesel");
  });
  it("maps 503 to a safe message and never leaks server text", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response('{"detail":"secret SQL"}', { status: 503 }))));
    await expect(getEnergyStationHistory("zesco_diesel", "Z")).rejects.toThrow("Stored ERB generation data is unavailable (database)");
  });
  it("rejects a malformed 200 body", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response('{"nope":1}'))));
    await expect(getEnergyStationHistory("zesco_diesel", "Z")).rejects.toThrow("invalid Energy response");
  });
});
