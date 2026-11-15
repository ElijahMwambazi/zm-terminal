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
    expect(screen.getByRole("status").textContent).toContain("Loading monthly averages");
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
