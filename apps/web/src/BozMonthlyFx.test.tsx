import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import BozMonthlyFx from "./BozMonthlyFx";
import UsdZmwDetail from "./UsdZmwDetail";
import type { FxMonthlyPoint } from "./api";

// jsdom has no canvas: the chart is replaced by a stub that exposes its inputs.
vi.mock("./MonthlyFxChart", async () => {
  const actual = await vi.importActual<typeof import("./MonthlyFxChart")>("./MonthlyFxChart");
  return {
    ...actual,
    default: ({ active, observations, onInspect }: { active: string[]; observations: { month: string }[]; onInspect: (m: string) => void }) => (
      <div data-testid="chart" data-series={active.join(",")} data-points={observations.length}>
        <button type="button" onClick={() => onInspect(observations[0].month)}>hover-first</button>
      </div>
    ),
  };
});
vi.mock("./PriceChart", () => ({ default: () => <div data-testid="intraday-chart" /> }));

const row = (month: string, buy: string, mid: string, sell: string): FxMonthlyPoint => ({
  month, buy, mid, sell, source: "boz", source_series: "fortnightly_table_02_monthly_average",
  source_publication: `Pub ${month}`, source_url: "https://www.boz.zm/sites/default/files/x.pdf", source_page: 3, published_precision: 2,
});
const ALL = [row("2019-03", "12.01", "12.04", "12.04"), row("2026-08", "19.00", "19.10", "19.20"), row("2026-09", "19.39", "19.42", "19.44")];
const body = (range: string, obs: FxMonthlyPoint[]) => ({
  symbol: "USDZMW", range, count: obs.length, first_month: obs[0]?.month ?? null, last_month: obs.at(-1)?.month ?? null, observations: obs,
});

let fetchMock: ReturnType<typeof vi.fn>;
const respond = (obs: FxMonthlyPoint[] = ALL, status = 200) => (url: string) =>
  Promise.resolve(new Response(JSON.stringify(body(new URL(url).searchParams.get("range") ?? "", obs)), { status }));
const requested = () => fetchMock.mock.calls.map(([url]) => new URL(String(url)).searchParams.get("range"));

beforeEach(() => { fetchMock = vi.fn(respond()); vi.stubGlobal("fetch", fetchMock); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("BozMonthlyFx", () => {
  it("renders API data with 1Y as the default range and labels it as not live", async () => {
    render(<BozMonthlyFx />);
    expect(await screen.findByText("Latest monthly average")).toBeTruthy();
    expect(requested()).toEqual(["1Y"]);
    expect(screen.getByRole("button", { name: "1Y" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText("MONTHLY · NOT LIVE")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "BoZ Monthly Average" })).toBeTruthy();
    expect(screen.getByTestId("chart").dataset.points).toBe("3");
  });

  it("shows the latest published mid, month and range metadata unchanged", async () => {
    render(<BozMonthlyFx />);
    expect((await screen.findByText("19.42", { selector: ".monthly-latest" })).textContent).toBe("19.42");
    expect(screen.getAllByText("Sep 2026").length).toBeGreaterThan(0);
    expect(screen.getByText("Mar 2019 – Sep 2026")).toBeTruthy();
    expect(screen.getByText("3 observations")).toBeTruthy();
  });

  it("does not alter the published mid even when it differs from the buy/sell midpoint", async () => {
    render(<BozMonthlyFx />);
    await screen.findByText("Latest monthly average");
    await userEvent.click(screen.getByText("hover-first")); // 2019-03: buy 12.01, mid 12.04, sell 12.04 (arithmetic midpoint 12.025)
    const readout = document.querySelector(".monthly-readout")!;
    expect(readout.textContent).toContain("Mar 2019");
    expect(within(readout as HTMLElement).getByText("12.04", { selector: "span:nth-of-type(2) b" })).toBeTruthy();
    expect(readout.textContent).not.toContain("12.025");
  });

  it("requests only backend range values when the range changes", async () => {
    render(<BozMonthlyFx />);
    await screen.findByText("Latest monthly average");
    for (const range of ["3Y", "5Y", "ALL"]) {
      await userEvent.click(screen.getByRole("button", { name: range }));
      await waitFor(() => expect(screen.getByRole("button", { name: range }).getAttribute("aria-pressed")).toBe("true"));
    }
    await waitFor(() => expect(requested()).toEqual(["1Y", "3Y", "5Y", "ALL"]));
    expect(requested().every((r) => ["1Y", "3Y", "5Y", "ALL"].includes(String(r)))).toBe(true);
  });

  it("starts with Mid, allows several series, and never leaves the chart empty", async () => {
    render(<BozMonthlyFx />);
    await screen.findByText("Latest monthly average");
    const series = () => screen.getByTestId("chart").dataset.series;
    expect(series()).toBe("mid");
    await userEvent.click(screen.getByRole("button", { name: "Buy" }));
    await userEvent.click(screen.getByRole("button", { name: "Sell" }));
    expect(series()).toBe("mid,buy,sell");
    expect(screen.getByRole("button", { name: "Buy" }).getAttribute("aria-pressed")).toBe("true");
    await userEvent.click(screen.getByRole("button", { name: "Mid" }));
    await userEvent.click(screen.getByRole("button", { name: "Buy" }));
    await userEvent.click(screen.getByRole("button", { name: "Sell" })); // last remaining series cannot be removed
    expect(series()).toBe("sell");
  });

  it("shows buy, mid, sell, attribution and source provenance for the selected month", async () => {
    render(<BozMonthlyFx />);
    await screen.findByText("Latest monthly average");
    const readout = document.querySelector(".monthly-readout")!;
    expect(readout.textContent).toContain("Buy 19.39");
    expect(readout.textContent).toContain("Mid 19.42");
    expect(readout.textContent).toContain("Sell 19.44");
    expect(readout.textContent).toContain("Bank of Zambia");
    expect(screen.getByText("Pub 2026-09")).toBeTruthy();
    expect(screen.getByText("3 (Table 02)")).toBeTruthy();
    expect(screen.getByText("2 decimal places, as published")).toBeTruthy();
    const link = screen.getByRole("link", { name: "Open Fortnightly Statistics PDF" });
    expect(link.getAttribute("href")).toBe("https://www.boz.zm/sites/default/files/x.pdf");
    expect(link.getAttribute("rel")).toContain("noopener");
    await userEvent.click(screen.getByRole("button", { name: "Previous month" }));
    expect(screen.getByText("Pub 2026-08")).toBeTruthy();
  });

  it("shows a loading placeholder first", () => {
    fetchMock.mockReturnValue(new Promise(() => {}));
    render(<BozMonthlyFx />);
    expect(screen.getByText("Loading monthly averages…")).toBeTruthy();
  });

  it("shows an independent error state", async () => {
    fetchMock.mockImplementation(respond([], 503));
    render(<BozMonthlyFx />);
    expect((await screen.findByRole("alert")).textContent).toContain("Historical BoZ monthly data could not be loaded");
    expect(screen.queryByTestId("chart")).toBeNull();
  });

  it("shows an empty state", async () => {
    fetchMock.mockImplementation(respond([]));
    render(<BozMonthlyFx />);
    expect(await screen.findByText("No monthly observations are available.")).toBeTruthy();
  });

  it("rejects a response whose count disagrees with its observations", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ ...body("1Y", ALL), count: 9 }))));
    render(<BozMonthlyFx />);
    expect((await screen.findByRole("alert")).textContent).toContain("Unexpected monthly response");
  });
});

describe("monthly response validation", () => {
  const good = (o: Partial<FxMonthlyPoint> = {}, i = 1) => ({ ...ALL[i], ...o });
  const cases: [string, (b: ReturnType<typeof body>) => unknown][] = [
    ["a null observation", (b) => ({ ...b, count: 1, first_month: "2026-09", last_month: "2026-09", observations: [null] })],
    ["a malformed decimal string", (b) => ({ ...b, observations: [ALL[0], ALL[1], good({ mid: "19.4x" }, 2)] })],
    ["NaN as a decimal", (b) => ({ ...b, observations: [ALL[0], ALL[1], good({ buy: "NaN" }, 2)] })],
    ["Infinity as a decimal", (b) => ({ ...b, observations: [ALL[0], ALL[1], good({ sell: "Infinity" }, 2)] })],
    ["an empty decimal string", (b) => ({ ...b, observations: [ALL[0], ALL[1], good({ mid: "" }, 2)] })],
    ["a numeric (non-string) decimal", (b) => ({ ...b, observations: [ALL[0], ALL[1], { ...ALL[2], mid: 19.42 }] })],
    ["an impossible month 2026-13", (b) => ({ ...b, last_month: "2026-13", observations: [ALL[0], ALL[1], good({ month: "2026-13" }, 2)] })],
    ["month 2026-00", (b) => ({ ...b, last_month: "2026-00", observations: [ALL[0], ALL[1], good({ month: "2026-00" }, 2)] })],
    ["a duplicate month", (b) => ({ ...b, observations: [ALL[0], ALL[2], ALL[2]], last_month: "2026-09" })],
    ["out-of-order months", (b) => ({ ...b, first_month: "2026-09", last_month: "2019-03", observations: [ALL[2], ALL[1], ALL[0]] })],
    ["an inconsistent count", (b) => ({ ...b, count: 2 })],
    ["an inconsistent first_month", (b) => ({ ...b, first_month: "2019-04" })],
    ["an inconsistent last_month", (b) => ({ ...b, last_month: "2026-08" })],
    ["a null first_month on a non-empty result", (b) => ({ ...b, first_month: null })],
    ["a non-null bound on an empty result", (b) => ({ ...b, count: 0, observations: [], first_month: "2019-03", last_month: "2026-09" })],
    ["a mismatched range", (b) => ({ ...b, range: "5Y" })],
    ["a wrong symbol", (b) => ({ ...b, symbol: "EURZMW" })],
    ["a missing provenance field", (b) => ({ ...b, observations: [ALL[0], ALL[1], { ...ALL[2], source_url: undefined }] })],
  ];
  it.each(cases)("shows the monthly error state for %s and keeps the live and intraday FX UI", async (_name, mutate) => {
    const quote = { symbol: "USDZMW", base_currency: "USD", quote_currency: "ZMW", buy: "20.4201", mid: "20.4451", sell: "20.4701",
      observed_at: "2026-11-13T13:30:00Z", retrieved_at: "2026-11-15T07:05:33Z", source: "boz", source_url: "https://www.boz.zm/x", stale: false };
    const hist = { symbol: "USDZMW", range: "1M", count: 2, observations: [0, 1].map((i) =>
      ({ observed_at: `2026-11-0${i + 1}T10:00:00Z`, buy: "20.1", mid: "20.2", sell: "20.3", source: "boz" })) };
    fetchMock.mockImplementation((url: string) => String(url).includes("/monthly")
      ? Promise.resolve(new Response(JSON.stringify(mutate(body("1Y", ALL)))))
      : Promise.resolve(new Response(JSON.stringify(String(url).includes("/history") ? hist : quote))));
    render(<UsdZmwDetail state={{ status: "ready", quote: quote as never }} refreshKey={1} onRefresh={() => {}} />);
    expect((await screen.findByRole("alert")).textContent).toContain("Historical BoZ monthly data could not be loaded");
    expect(screen.queryByTestId("chart")).toBeNull();
    expect(screen.getByText("20.4451")).toBeTruthy();
    expect(await screen.findByTestId("intraday-chart")).toBeTruthy();
  });

  it("accepts the backend's empty contract (count 0, null bounds)", async () => {
    fetchMock.mockImplementation(respond([]));
    render(<BozMonthlyFx />);
    expect(await screen.findByText("No monthly observations are available.")).toBeTruthy();
  });
});

describe("source-series provenance labelling", () => {
  const annual = (month: string, extra: Partial<FxMonthlyPoint> = {}): FxMonthlyPoint => ({
    ...row(month, "5.28360", "5.24097", "5.25097"), source_series: "annual_report_table_13_monthly_average",
    source_publication: `Annual ${month}`, source_url: "https://www.boz.zm/sites/default/files/annual.pdf", source_page: 148, ...extra,
  });
  const show = async (obs: FxMonthlyPoint[]) => {
    fetchMock.mockImplementation(respond(obs));
    render(<BozMonthlyFx />);
    await screen.findByText("Latest monthly average");
    return document.querySelector(".monthly-readout")!;
  };

  it("labels a pre-2013 Annual Report row as Table 13 / Annual Report and explains the ZMK normalization", async () => {
    const readout = await show([annual("2012-04")]);
    expect(screen.getByText("148 (Table 13)")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open Annual Report PDF" })).toBeTruthy();
    expect(readout.textContent).not.toContain("Table 02");
    expect(readout.textContent).not.toContain("Fortnightly Statistics PDF");
    expect(readout.textContent).toContain("Normalized to ZMW from the published ZMK values");
    expect(readout.textContent).not.toContain("as published");
  });

  it("keeps 'as published' wording for a 2013 Annual Report row already published in ZMW", async () => {
    const readout = await show([annual("2013-12")]);
    expect(screen.getByText("148 (Table 13)")).toBeTruthy();
    expect(readout.textContent).toContain("2 decimal places, as published");
    expect(readout.textContent).not.toContain("ZMK");
  });

  it("keeps Table 02 / Fortnightly wording for a 2014+ row", async () => {
    const readout = await show([row("2014-01", "5.52", "5.53", "5.53")]);
    expect(screen.getByText("3 (Table 02)")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open Fortnightly Statistics PDF" })).toBeTruthy();
    expect(readout.textContent).not.toContain("Table 13");
    expect(readout.textContent).not.toContain("Normalized");
  });

  it("uses neutral wording for an unknown source series instead of claiming a table", async () => {
    const readout = await show([row("2026-09", "19.39", "19.42", "19.44")].map((o) => ({ ...o, source_series: "future_series" })));
    expect(screen.getByText("3 (official BoZ table)")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open source PDF" })).toBeTruthy();
    expect(readout.textContent).not.toContain("Table 02");
    expect(readout.textContent).not.toContain("Table 13");
  });
});

describe("range refetch labelling", () => {
  const year = ALL.slice(1);
  const deferred = () => { let resolve!: (r: Response) => void; const promise = new Promise<Response>((r) => { resolve = r; }); return { promise, resolve }; };
  const json = (range: string, obs: FxMonthlyPoint[]) => new Response(JSON.stringify(body(range, obs)));

  it("says 5Y is loading while the previous 1Y data stays displayed, then switches together", async () => {
    const slow = deferred();
    fetchMock.mockImplementation((url: string) => new URL(url).searchParams.get("range") === "5Y" ? slow.promise : Promise.resolve(json("1Y", year)));
    render(<BozMonthlyFx />);
    await screen.findByText("Latest monthly average");
    expect(screen.getByText("Displayed range · 1Y")).toBeTruthy();
    expect(screen.queryByText(/Loading 5Y/)).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "5Y" }));
    expect((await screen.findByText("Loading 5Y… showing previous 1Y data")).textContent).toBeTruthy();
    expect(screen.getByRole("button", { name: "5Y" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText("Displayed range · 1Y")).toBeTruthy(); // old data is never labelled 5Y
    expect(screen.queryByText("Displayed range · 5Y")).toBeNull();
    expect(screen.getByTestId("chart").dataset.points).toBe("2");
    expect(document.querySelector("[aria-busy='true']")).not.toBeNull();

    slow.resolve(json("5Y", ALL));
    expect(await screen.findByText("Displayed range · 5Y")).toBeTruthy();
    expect(screen.queryByText(/Loading 5Y/)).toBeNull();
    expect(screen.getByTestId("chart").dataset.points).toBe("3");
    expect(screen.getByText("3 observations")).toBeTruthy();
  });

  it("never presents previous data as the failed range when the delayed request fails", async () => {
    const slow = deferred();
    fetchMock.mockImplementation((url: string) => new URL(url).searchParams.get("range") === "5Y" ? slow.promise : Promise.resolve(json("1Y", year)));
    render(<BozMonthlyFx />);
    await screen.findByText("Latest monthly average");
    await userEvent.click(screen.getByRole("button", { name: "5Y" }));
    await screen.findByText("Loading 5Y… showing previous 1Y data");
    slow.resolve(new Response("{}", { status: 503 }));
    expect((await screen.findByRole("alert")).textContent).toContain("Historical BoZ monthly data could not be loaded");
    expect(screen.queryByText(/showing previous/)).toBeNull();
    expect(screen.queryByTestId("chart")).toBeNull();
    expect(screen.queryByText("Displayed range · 1Y")).toBeNull();
  });
});

describe("UsdZmwDetail", () => {
  it("still renders the live quote and intraday history next to the monthly section, and survives monthly failure", async () => {
    const quote = { symbol: "USDZMW", base_currency: "USD", quote_currency: "ZMW", buy: "20.4201", mid: "20.4451", sell: "20.4701",
      observed_at: "2026-11-13T13:30:00Z", retrieved_at: "2026-11-15T07:05:33Z", source: "boz", source_url: "https://www.boz.zm/x", stale: false };
    const hist = (n: number) => ({ symbol: "USDZMW", range: "1M", count: n, observations: Array.from({ length: n }, (_, i) =>
      ({ observed_at: `2026-11-0${i + 1}T10:00:00Z`, buy: "20.1", mid: "20.2", sell: "20.3", source: "boz" })) });
    fetchMock.mockImplementation((url: string) =>
      String(url).includes("/monthly") ? Promise.resolve(new Response("{}", { status: 503 }))
        : Promise.resolve(new Response(JSON.stringify(String(url).includes("/history") ? hist(3) : quote))));
    render(<UsdZmwDetail state={{ status: "ready", quote: quote as never }} refreshKey={1} onRefresh={() => {}} />);
    expect(screen.getByText("20.4451")).toBeTruthy();
    expect(screen.getByText("FRESH")).toBeTruthy();
    expect(await screen.findByTestId("intraday-chart")).toBeTruthy();
    expect(screen.getByRole("button", { name: "1D" })).toBeTruthy(); // intraday ranges unchanged
    expect((await screen.findByRole("alert")).textContent).toContain("Historical BoZ monthly data could not be loaded");
  });
});
