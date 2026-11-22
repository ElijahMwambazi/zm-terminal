import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import BankFxDetail from "./BankFxDetail";
import type { BankFxState } from "./useBankFx";
import { parseBankFxLatest } from "./api";
import { row, slot, snapshot } from "./bankFxFixtures";

vi.mock("./PriceChart", () => ({ default: () => <div /> }));
vi.mock("./AuctionChart", () => ({ default: () => <div /> }));
vi.mock("./EnergyAnnualChart", () => ({ default: () => <div /> }));

const ready = (s: unknown = snapshot()): BankFxState => ({ status: "ready", data: parseBankFxLatest({ snapshot: s }) });
let routes: Record<string, (params: URLSearchParams) => Response | Promise<Response>>;
let calls: string[];
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

beforeEach(() => {
  calls = [];
  routes = {};
  vi.stubGlobal("fetch", vi.fn((url: string) => {
    const u = new URL(String(url));
    calls.push(u.pathname + u.search);
    const handler = routes[u.pathname];
    return handler ? Promise.resolve(handler(u.searchParams)) : Promise.reject(new Error("no route"));
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const rowNames = () => Array.from(document.querySelectorAll(".bank-fx-table tbody th")).map((th) => th.textContent);
const slotButtons = () => within(screen.getByRole("group", { name: "Source slot" })).getAllByRole("button");
const pressed = () => slotButtons().filter((b) => b.getAttribute("aria-pressed") === "true").map((b) => b.textContent);
const detail = (state: BankFxState = ready(), onRefresh = () => {}) => render(<BankFxDetail state={state} onRefresh={onRefresh} />);

describe("BankFxDetail states", () => {
  it("shows loading, error and empty states", () => {
    const { rerender } = render(<BankFxDetail state={{ status: "loading" }} onRefresh={() => {}} />);
    expect(screen.getByText("Loading stored Bank FX…")).toBeTruthy();
    rerender(<BankFxDetail state={{ status: "error", message: "Stored commercial bank FX data is unavailable (database)" }} onRefresh={() => {}} />);
    expect(screen.getByRole("alert").textContent).toContain("unavailable (database)");
    expect(document.querySelector(".bank-fx-table")).toBeNull();
    rerender(<BankFxDetail state={ready(null)} onRefresh={() => {}} />);
    expect(screen.getByText("No stored commercial-bank FX.")).toBeTruthy();
  });
  it("is a focusable labelled article with the safe notices", () => {
    detail();
    const article = screen.getByLabelText("Commercial bank FX detail");
    expect(article.getAttribute("tabindex")).toBe("-1");
    expect(article.textContent).toContain("BANK FX · COMMERCIAL BANK USD/ZMW");
    expect(article.textContent).toContain("never contacts BoZ");
    expect(article.textContent).toContain("not a live quote");
    expect(article.textContent).toContain("ZMW per USD");
    expect(article.textContent).toContain("ZM Terminal does not assign a source timezone");
  });
});

describe("slot selection", () => {
  it("defaults to the latest published slot: 15:30 / 12:30 / 09:30", () => {
    for (const [slots, expected] of [[["09:30", "12:30", "15:30"], "15:30"], [["09:30", "12:30"], "12:30"], [["09:30"], "09:30"]] as const) {
      const { unmount } = detail(ready(snapshot({}, [...slots])));
      expect(slotButtons().map((b) => b.textContent)).toEqual([...slots]);
      expect(pressed()).toEqual([expected]);
      unmount();
    }
  });
  it("switches the displayed rows and never invents a missing slot", async () => {
    detail(ready(snapshot({}, ["09:30", "12:30"])));
    const first = screen.getByText("ZULU TEST BANK").closest("tr")!.textContent;
    await userEvent.click(screen.getByRole("button", { name: "09:30" }));
    expect(screen.getByText("ZULU TEST BANK").closest("tr")!.textContent).not.toBe(first);
    expect(screen.queryByRole("button", { name: "15:30" })).toBeNull();
  });
  it("labels slots without any timezone", () => {
    detail();
    for (const b of slotButtons()) expect(b.textContent).toMatch(/^\d\d:\d\d$/);
    const scope = [screen.getByRole("group", { name: "Source slot" }), screen.getByLabelText("Bank rates 15:30")];
    for (const el of scope) expect(el.textContent).not.toMatch(/CAT|UTC|GMT|local time/i);
    expect(screen.getByLabelText("Commercial bank FX detail").textContent).not.toMatch(/(?:09|12|15):30\s*(CAT|UTC|GMT)/);
  });
});

describe("table content", () => {
  it("keeps source row order, not alphabetical order", () => {
    detail();
    expect(rowNames()).toEqual(["ZULU TEST BANK", "BETA FINANCE", "ALPHA BANK"]);
  });
  it("shows exact source strings and never reformats them", () => {
    const s = snapshot({ slots: [{ ...slot("09:30", 10), banks: [row(3, "ONE BANK", 9, { interbank_buy: "9.9", interbank_sell: "9.90", retail_buy: "9.900", retail_sell: "9.9000" })] }] });
    detail(ready(s));
    const cells = Array.from(screen.getByText("ONE BANK").closest("tr")!.querySelectorAll("td")).map((td) => td.textContent);
    expect(cells).toEqual(["9.9", "9.90", "9.900", "9.9000"]);
  });
  it("uses the normalised institution name, not the raw spacing", () => {
    detail(ready(snapshot({ slots: [{ ...slot("09:30", 10), banks: [row(3, "ALPHA BANK", 9, { institution_name_raw: "ALPHA   BANK  " })] }] })));
    expect(rowNames()).toEqual(["ALPHA BANK"]);
  });
  it("has real column/row header semantics", () => {
    detail();
    const heads = screen.getAllByRole("columnheader").map((h) => h.textContent);
    expect(heads).toEqual(expect.arrayContaining(["BANK", "INTERBANK", "BUY", "SELL"]));
    expect(screen.getAllByRole("rowheader").length).toBe(3);
  });
  it("shows the source's own retail label for v1 and v2", () => {
    detail(ready(snapshot({}, ["09:30"], "v2")));
    expect(screen.getByText("RETAIL (BoZ: BOARD RATES)")).toBeTruthy();
    cleanup();
    detail(ready(snapshot({}, ["09:30"], "v1")));
    expect(screen.getByText("RETAIL (BoZ: Retail Rates)")).toBeTruthy();
    expect(screen.queryByText(/BOARD RATES/)).toBeNull();
  });
});

describe("market average", () => {
  it("is a separate section and absent from the bank table", () => {
    detail();
    const section = screen.getByLabelText("BoZ-published market average");
    expect(section.textContent).toContain("BOZ-PUBLISHED MARKET AVERAGE");
    expect(rowNames()).not.toContain("MARKET AVERAGE");
    expect(document.querySelector(".bank-fx-table")!.textContent).not.toContain("MARKET AVERAGE");
    const values = Array.from(section.querySelectorAll("dd")).map((d) => d.textContent);
    expect(values.every((v) => /^\d+\.\d{2}$/.test(v ?? ""))).toBe(true);
    expect(section.textContent).not.toMatch(/computed|calculated by|ZM Terminal average|benchmark/i);
  });
  it("uses the exact published strings", () => {
    const s = snapshot({ slots: [slot("09:30", 10, { interbank_buy: "8.5", interbank_sell: "8.50", retail_buy: "8.500", retail_sell: "8.5000" })] });
    detail(ready(s));
    const values = Array.from(screen.getByLabelText("BoZ-published market average").querySelectorAll("dd")).map((d) => d.textContent);
    expect(values).toEqual(["8.5", "8.50", "8.500", "8.5000"]);
  });
  it("handles a slot with no published average without zeros or blanks", () => {
    detail(ready(snapshot({ slots: [slot("09:30", 10, false)] })));
    const section = screen.getByLabelText("BoZ-published market average");
    expect(section.textContent).toContain("Market average not published for this slot.");
    expect(section.querySelectorAll("dd")).toHaveLength(0);
  });
});

describe("no derived analytics", () => {
  it("introduces no ranking, spread or best-rate wording", () => {
    detail();
    const text = screen.getByLabelText("Commercial bank FX detail").textContent!;
    expect(text).not.toMatch(/best|spread|ranking|cheapest|highest|lowest|midpoint|recommend/i);
  });
});

describe("rights and provenance", () => {
  it("review_required gets the local/internal review notice", () => {
    detail();
    const text = screen.getByLabelText("Source and rights").textContent!;
    expect(text).toContain("LOCAL / INTERNAL · RIGHTS REVIEW REQUIRED");
    expect(text).toContain("not represented as cleared");
  });
  it("another status is shown neutrally and literally", () => {
    detail(ready(snapshot({ rights_status: "cleared_for_test" })));
    const text = screen.getByLabelText("Source and rights").textContent!;
    expect(text).toContain("LOCAL / INTERNAL · RIGHTS STATUS");
    expect(text).toContain("rights_status = cleared_for_test");
    expect(text).not.toContain("RIGHTS REVIEW REQUIRED");
  });
  it("links to the API-supplied source URL and shows the raw datetime, layout and revision", () => {
    detail();
    const link = screen.getByRole("link", { name: "Bank of Zambia" });
    expect(link.getAttribute("href")).toBe("https://example.test/commercial-bank-rates");
    const text = screen.getByLabelText("Source and rights").textContent!;
    expect(text).toContain("2030-01-02T15:36:15+02:00");
    expect(text).toContain("SYNTHETIC RATES 02 01 30");
    expect(text).toContain("11 · r22");
    expect(text).toContain("00000000-0000-4000-8000-0000000000a1"); // technical <details>
  });
});

describe("exact stored date", () => {
  const dated = (day: string, slots: ("09:30" | "12:30" | "15:30")[] = ["09:30", "12:30", "15:30"]) =>
    snapshot({ observation_date: day, source_record_id: `00000000-0000-4000-8000-${day.replace(/-/g, "").padEnd(12, "0")}` }, slots);

  it("starts on the displayed snapshot's date", () => {
    detail();
    expect((screen.getByLabelText("STORED DATE") as HTMLInputElement).value).toBe("2030-01-02");
    expect((screen.getByRole("button", { name: "LATEST STORED" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("loads an exact stored date, then returns to latest", async () => {
    routes["/api/v1/fx/banks/history"] = (p) => json(dated(p.get("date")!, ["09:30"]));
    const user = userEvent.setup();
    detail();
    await user.clear(screen.getByLabelText("STORED DATE"));
    await user.type(screen.getByLabelText("STORED DATE"), "2029-12-31");
    await user.click(screen.getByRole("button", { name: "LOAD DATE" }));
    await waitFor(() => expect(screen.getByText(/\(2029-12-31\)/)).toBeTruthy());
    expect(calls).toEqual(["/api/v1/fx/banks/history?date=2029-12-31"]);
    expect(pressed()).toEqual(["09:30"]);
    await user.click(screen.getByRole("button", { name: "LATEST STORED" }));
    expect(screen.getByText(/\(2030-01-02\)/)).toBeTruthy();
    expect((screen.getByLabelText("STORED DATE") as HTMLInputElement).value).toBe("2030-01-02");
    expect(calls).toHaveLength(1); // returning to latest needs no extra request
  });
  it("preserves the selected slot when the new snapshot has it, else uses the latest", async () => {
    routes["/api/v1/fx/banks/history"] = (p) => json(dated(p.get("date")!, p.get("date") === "2029-12-31" ? ["09:30", "12:30"] : ["09:30"]));
    const user = userEvent.setup();
    detail();
    await user.click(screen.getByRole("button", { name: "12:30" }));
    await user.clear(screen.getByLabelText("STORED DATE")); await user.type(screen.getByLabelText("STORED DATE"), "2029-12-31");
    await user.click(screen.getByRole("button", { name: "LOAD DATE" }));
    await waitFor(() => expect(screen.getByText(/\(2029-12-31\)/)).toBeTruthy());
    expect(pressed()).toEqual(["12:30"]);
    await user.clear(screen.getByLabelText("STORED DATE")); await user.type(screen.getByLabelText("STORED DATE"), "2029-12-30");
    await user.click(screen.getByRole("button", { name: "LOAD DATE" }));
    await waitFor(() => expect(screen.getByText(/\(2029-12-30\)/)).toBeTruthy());
    expect(pressed()).toEqual(["09:30"]);
  });
  it("says so when the exact date is not stored, uses no fallback and keeps the snapshot visible", async () => {
    routes["/api/v1/fx/banks/history"] = () => json({ detail: "x" }, 404);
    const user = userEvent.setup();
    detail();
    await user.clear(screen.getByLabelText("STORED DATE")); await user.type(screen.getByLabelText("STORED DATE"), "2029-11-19");
    await user.click(screen.getByRole("button", { name: "LOAD DATE" }));
    expect(await screen.findByText("No stored commercial-bank FX snapshot for 2029-11-19. No fallback date was used.")).toBeTruthy();
    expect(calls).toEqual(["/api/v1/fx/banks/history?date=2029-11-19"]); // exactly one request, for exactly that date
    expect(screen.getByText(/\(2030-01-02\)/)).toBeTruthy(); // the earlier snapshot is still what is displayed
  });
  it("treats an HTTP 200 null history body as a malformed response, not a missing date, and keeps the snapshot", async () => {
    routes["/api/v1/fx/banks/history"] = () => new Response("null", { status: 200 });
    const user = userEvent.setup();
    detail();
    await user.clear(screen.getByLabelText("STORED DATE")); await user.type(screen.getByLabelText("STORED DATE"), "2029-11-19");
    await user.click(screen.getByRole("button", { name: "LOAD DATE" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Unexpected Bank FX response");
    expect(screen.queryByText(/No stored commercial-bank FX snapshot/)).toBeNull();
    expect(screen.getByText(/\(2030-01-02\)/)).toBeTruthy();
  });
  it("shows a safe error for a database failure", async () => {
    routes["/api/v1/fx/banks/history"] = () => json({ detail: "postgresql://secret" }, 503);
    const user = userEvent.setup();
    detail();
    await user.click(screen.getByRole("button", { name: "LOAD DATE" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Stored commercial bank FX data is unavailable (database)");
    expect(alert.textContent).not.toContain("secret");
  });
  it("a slow earlier date never overwrites a newer one", async () => {
    let releaseA: () => void = () => {};
    routes["/api/v1/fx/banks/history"] = (p) => p.get("date") === "2029-12-01"
      ? new Promise<Response>((resolve) => { releaseA = () => resolve(json(dated("2029-12-01"))); })
      : json(dated("2029-12-02"));
    const user = userEvent.setup();
    detail();
    const input = screen.getByLabelText("STORED DATE");
    await user.clear(input); await user.type(input, "2029-12-01");
    await user.click(screen.getByRole("button", { name: "LOAD DATE" }));
    await user.clear(input); await user.type(input, "2029-12-02");
    await user.click(screen.getByRole("button", { name: "LOAD DATE" }));
    await waitFor(() => expect(screen.getByText(/\(2029-12-02\)/)).toBeTruthy());
    await act(async () => { releaseA(); await Promise.resolve(); });
    expect(screen.getByText(/\(2029-12-02\)/)).toBeTruthy();
    expect(screen.queryByText(/\(2029-12-01\)/)).toBeNull();
  });
  it("LATEST STORED drops a pending exact-date read", async () => {
    let release: () => void = () => {};
    routes["/api/v1/fx/banks/history"] = () => new Promise<Response>((resolve) => { release = () => resolve(json(dated("2029-12-01"))); });
    const user = userEvent.setup();
    detail();
    await user.clear(screen.getByLabelText("STORED DATE")); await user.type(screen.getByLabelText("STORED DATE"), "2029-12-01");
    await user.click(screen.getByRole("button", { name: "LOAD DATE" }));
    await user.click(screen.getByRole("button", { name: "LATEST STORED" }));
    await act(async () => { release(); await Promise.resolve(); });
    expect(screen.getByText(/\(2030-01-02\)/)).toBeTruthy();
  });
  it("an unmount drops a pending read without error", async () => {
    let release: () => void = () => {};
    routes["/api/v1/fx/banks/history"] = () => new Promise<Response>((resolve) => { release = () => resolve(json(dated("2029-12-01"))); });
    const user = userEvent.setup();
    const { unmount } = detail();
    await user.click(screen.getByRole("button", { name: "LOAD DATE" }));
    unmount();
    await act(async () => { release(); await Promise.resolve(); });
  });
});

describe("re-read stored", () => {
  it("on latest it calls the supplied refresh and nothing else", async () => {
    const onRefresh = vi.fn();
    detail(ready(), onRefresh);
    await userEvent.click(screen.getByRole("button", { name: "RE-READ STORED" }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
    expect(calls).toEqual([]);
  });
  it("on an exact date it re-reads that same date and not latest", async () => {
    routes["/api/v1/fx/banks/history"] = (p) => json(snapshot({ observation_date: p.get("date")! }));
    const onRefresh = vi.fn();
    const user = userEvent.setup();
    detail(ready(), onRefresh);
    await user.clear(screen.getByLabelText("STORED DATE")); await user.type(screen.getByLabelText("STORED DATE"), "2029-12-31");
    await user.click(screen.getByRole("button", { name: "LOAD DATE" }));
    await waitFor(() => expect(screen.getByText(/\(2029-12-31\)/)).toBeTruthy());
    await user.click(screen.getByRole("button", { name: "RE-READ STORED" }));
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls).toEqual(["/api/v1/fx/banks/history?date=2029-12-31", "/api/v1/fx/banks/history?date=2029-12-31"]);
    expect(onRefresh).not.toHaveBeenCalled();
    expect(screen.getByText(/\(2029-12-31\)/)).toBeTruthy();
  });
});

describe("dashboard card and App integration", () => {
  const appRoutes = (body: unknown = { snapshot: snapshot() }, status = 200) => { routes["/api/v1/fx/banks/latest"] = () => json(body, status); };

  it("shows the latest stored date, slot count and rights, with no rate", async () => {
    appRoutes();
    render(<App />);
    const card = (await screen.findByText("BANK FX", { selector: ".market-label" })).closest("button")!;
    await waitFor(() => expect(card.textContent).toContain("02 Jan 2030"));
    expect(card.textContent).toContain("LATEST STORED · 3 PUBLISHED SLOTS");
    expect(card.textContent).toContain("local/internal");
    expect(card.textContent).toContain("Rights review required");
    expect(card.textContent).not.toMatch(/\d+\.\d{2}/);
    expect(card.textContent).not.toMatch(/average|best|spread/i);
  });
  it("uses the singular for one slot and a neutral rights label for another status", async () => {
    appRoutes({ snapshot: snapshot({ rights_status: "x_status" }, ["09:30"]) });
    render(<App />);
    const card = (await screen.findByText("BANK FX", { selector: ".market-label" })).closest("button")!;
    await waitFor(() => expect(card.textContent).toContain("1 PUBLISHED SLOT"));
    expect(card.textContent).not.toContain("1 PUBLISHED SLOTS");
    expect(card.textContent).toContain("Rights x_status");
  });
  it("shows loading, error and empty states", async () => {
    routes["/api/v1/fx/banks/latest"] = () => json({}, 503);
    const { unmount } = render(<App />);
    expect(await screen.findByText("Stored Bank FX unavailable")).toBeTruthy();
    unmount();
    appRoutes({ snapshot: null });
    render(<App />);
    expect(await screen.findByText("No stored commercial-bank FX")).toBeTruthy();
  });
  it("shows an error for a malformed 200 and never a rate", async () => {
    appRoutes({ snapshot: snapshot({ slots: [{ ...slot("09:30", 10), banks: [row(3, "A", 9, { retail_buy: 9.9 as never })] }] }) });
    render(<App />);
    expect(await screen.findByText("Stored Bank FX unavailable")).toBeTruthy();
  });
  it("opens the detail from the card and from both commands", async () => {
    appRoutes();
    const user = userEvent.setup();
    render(<App />);
    await user.click((await screen.findByText("BANK FX", { selector: ".market-label" })).closest("button")!);
    expect(await screen.findByLabelText("Commercial bank FX detail")).toBeTruthy();
    cleanup();
    for (const command of ["bank fx", "fx banks"]) {
      render(<App />);
      await user.type(screen.getByLabelText("Terminal command"), `${command}{Enter}`);
      expect(await screen.findByLabelText("Commercial bank FX detail")).toBeTruthy();
      cleanup();
    }
  });
  it("makes no BoZ request and calls only the stored Bank FX endpoints", async () => {
    appRoutes();
    render(<App />);
    await screen.findByText("BANK FX", { selector: ".market-label" });
    await waitFor(() => expect(calls.some((c) => c.startsWith("/api/v1/fx/banks/latest"))).toBe(true));
    expect(calls.filter((c) => c.includes("banks"))).toEqual(["/api/v1/fx/banks/latest"]);
    expect((fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.every(([u]) => !/boz\.zm/i.test(String(u)))).toBe(true);
  });
  it("reopening resets the detail to the latest stored snapshot", async () => {
    appRoutes();
    routes["/api/v1/fx/banks/history"] = (p) => json(snapshot({ observation_date: p.get("date")! }));
    const user = userEvent.setup();
    render(<App />);
    const card = (await screen.findByText("BANK FX", { selector: ".market-label" })).closest("button")!;
    await waitFor(() => expect(card.textContent).toContain("02 Jan 2030"));
    await user.click(card);
    const input = await screen.findByLabelText("STORED DATE");
    await user.clear(input); await user.type(input, "2029-12-31");
    await user.click(screen.getByRole("button", { name: "LOAD DATE" }));
    await waitFor(() => expect(screen.getByText(/\(2029-12-31\)/)).toBeTruthy());
    await user.click(card);
    await waitFor(() => expect(screen.getByText(/\(2030-01-02\)/)).toBeTruthy());
  });
});
