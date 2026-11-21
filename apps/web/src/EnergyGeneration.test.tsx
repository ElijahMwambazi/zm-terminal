import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import EnergyGenerationDetail from "./EnergyGenerationDetail";
import type { EnergyState } from "./useEnergyGeneration";
import { parseEnergyLatest } from "./api";
import { ghistory, gpoint, history, hpoint, snapshot, station } from "./energyFixtures";

vi.mock("./EnergyAnnualChart", () => ({
  default: ({ points, label }: { points: { year: number; value: string | null }[]; label: string }) => (
    <div data-testid="energy-chart" aria-label={label} data-values={points.map((p) => `${p.year}:${p.value}`).join(",")} />
  ),
}));
vi.mock("./PriceChart", () => ({ default: () => <div /> }));
vi.mock("./AuctionChart", () => ({ default: () => <div /> }));

const ready = (s: unknown = snapshot()): EnergyState => ({ status: "ready", data: parseEnergyLatest({ snapshot: s }) });
let routes: Record<string, (params: URLSearchParams) => Response | Promise<Response>>;
let calls: string[];
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

beforeEach(() => {
  calls = [];
  routes = {
    "/api/v1/energy/generation/history": (p) => json(history(p.get("station_group")!, p.get("station_name")!)),
    "/api/v1/energy/generation/group-history": (p) => json(ghistory(p.get("station_group")!)),
  };
  vi.stubGlobal("fetch", vi.fn((url: string) => {
    const u = new URL(String(url));
    calls.push(u.pathname + u.search);
    const handler = routes[u.pathname];
    return handler ? Promise.resolve(handler(u.searchParams)) : Promise.reject(new Error("no route"));
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("EnergyGenerationDetail", () => {
  it("shows loading, error and empty states", () => {
    const { rerender } = render(<EnergyGenerationDetail state={{ status: "loading" }} onRefresh={() => {}} />);
    expect(screen.getByText("Loading stored ERB generation…")).toBeTruthy();
    rerender(<EnergyGenerationDetail state={{ status: "error", message: "Stored ERB generation data is unavailable (database)" }} onRefresh={() => {}} />);
    expect(screen.getByRole("alert").textContent).toContain("No Energy values are shown");
    rerender(<EnergyGenerationDetail state={{ status: "ready", data: { snapshot: null } }} onRefresh={() => {}} />);
    expect(screen.getByText("No stored ERB generation.")).toBeTruthy();
  });

  it("renders the overview, four published totals and no national sum", async () => {
    render(<EnergyGenerationDetail state={ready()} onRefresh={() => {}} />);
    const overview = screen.getByLabelText("Publication overview");
    expect(within(overview).getByText("2030")).toBeTruthy();
    expect(within(overview).getByText("2031 · r1")).toBeTruthy();
    const totals = screen.getByLabelText("Published group totals");
    expect(within(totals).getByText("400.10 GWh")).toBeTruthy();
    expect(within(totals).getByText("2.00 GWh")).toBeTruthy();
    expect(within(totals).getAllByText("published total")).toHaveLength(4);
    expect(screen.queryByText(/national/i, { selector: "strong, dd, td" })).toBeNull();
    expect(document.body.textContent).not.toContain("403.60"); // 400.10 + 2.00 + 0.00 + 1.50 is never shown
    await screen.findByLabelText("Station annual history");
  });

  it("marks a missing group total as not returned instead of zero", () => {
    render(<EnergyGenerationDetail state={ready(snapshot({ group_totals: [] }))} onRefresh={() => {}} />);
    expect(within(screen.getByLabelText("Published group totals")).getAllByText("not returned")).toHaveLength(4);
  });

  it("shows exact strings, reported zero and not_reported distinctly", () => {
    render(<EnergyGenerationDetail state={ready()} onRefresh={() => {}} />);
    const table = screen.getByLabelText("Station generation");
    expect(within(table).getByText("393.40 GWh")).toBeTruthy();
    expect(within(table).getByText("0.00 GWh")).toBeTruthy();
    expect(within(table).getByText("— NOT REPORTED")).toBeTruthy();
  });

  it("keeps API order and shows the rights notice neutrally", () => {
    render(<EnergyGenerationDetail state={ready(snapshot({ rights_status: "other_state" }))} onRefresh={() => {}} />);
    const names = within(screen.getByLabelText("Station generation")).getAllByRole("button").map((b) => b.textContent);
    expect(names).toEqual(["Alpha", "Beta", "Gamma", "Delta & Co"]);
    expect(screen.getAllByText(/rights_status = other_state/).length).toBeGreaterThan(0);
    expect(screen.getByText("LOCAL / INTERNAL · RIGHTS STATUS")).toBeTruthy();
  });

  it("loads the first station by default and the exact selected identity on click", async () => {
    const user = userEvent.setup();
    render(<EnergyGenerationDetail state={ready()} onRefresh={() => {}} />);
    await waitFor(() => expect(calls).toContain("/api/v1/energy/generation/history?station_group=zesco_large_hydro&station_name=Alpha"));
    await user.click(screen.getByRole("button", { name: "Delta & Co" }));
    await waitFor(() => expect(calls).toContain("/api/v1/energy/generation/history?station_group=independent_power_producer&station_name=Delta+%26+Co"));
    expect(screen.getByRole("button", { name: "Delta & Co" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "Delta & Co" }).closest("tr")!.className).toContain("row--focus");
  });

  it("renders a not_reported year as a gap (null) and exact history strings", async () => {
    render(<EnergyGenerationDetail state={ready()} onRefresh={() => {}} />);
    const chart = await screen.findByLabelText(/Alpha annual generation/);
    expect(chart.getAttribute("data-values")).toBe("2029:10.10,2030:null,2031:12.00");
    const section = screen.getByLabelText("Station annual history");
    expect(within(section).getByText("10.10")).toBeTruthy();
    expect(within(section).getByText("— NOT REPORTED")).toBeTruthy();
    expect(within(section).getAllByText("2031 r1").length).toBeGreaterThan(0);
  });

  it("explains an all-not-reported and a one-point history", async () => {
    routes["/api/v1/energy/generation/history"] = (p) => json(history(p.get("station_group")!, p.get("station_name")!, [hpoint(2029, null), hpoint(2030, null)]));
    render(<EnergyGenerationDetail state={ready()} onRefresh={() => {}} />);
    expect(await screen.findByText(/No reported values/)).toBeTruthy();
    expect(screen.queryByLabelText(/Alpha annual generation/)).toBeNull();
    cleanup();
    routes["/api/v1/energy/generation/history"] = (p) => json(history(p.get("station_group")!, p.get("station_name")!, [hpoint(2029, null), hpoint(2030, "1.00")]));
    render(<EnergyGenerationDetail state={ready()} onRefresh={() => {}} />);
    expect(await screen.findByText(/Single reported observation: no line is drawn/)).toBeTruthy();
    expect(screen.queryByLabelText(/Alpha annual generation/)).toBeNull();
    expect(screen.getByText("1.00")).toBeTruthy();
  });

  it("keeps the rest usable when station history fails or is empty", async () => {
    routes["/api/v1/energy/generation/history"] = () => json({ detail: "secret" }, 503);
    const { unmount } = render(<EnergyGenerationDetail state={ready()} onRefresh={() => {}} />);
    expect((await screen.findAllByRole("alert"))[0].textContent).toContain("Stored ERB generation data is unavailable (database)");
    expect(document.body.textContent).not.toContain("secret");
    expect(screen.getByLabelText("Station generation")).toBeTruthy();
    expect(await screen.findByLabelText(/ZESCO LARGE HYDRO published annual total/)).toBeTruthy(); // group history unaffected
    unmount();
    routes["/api/v1/energy/generation/history"] = (p) => json(history(p.get("station_group")!, p.get("station_name")!, []));
    render(<EnergyGenerationDetail state={ready()} onRefresh={() => {}} />);
    expect(await screen.findByText("No stored annual values for this station.")).toBeTruthy();
  });

  it("defaults the group to the selected station's group and loads the chosen group", async () => {
    const user = userEvent.setup();
    render(<EnergyGenerationDetail state={ready()} onRefresh={() => {}} />);
    await waitFor(() => expect(calls).toContain("/api/v1/energy/generation/group-history?station_group=zesco_large_hydro"));
    const picker = screen.getByRole("group", { name: "Station group" });
    await user.click(within(picker).getByRole("button", { name: /ZESCO DIESEL|DIESEL/ }));
    await waitFor(() => expect(calls).toContain("/api/v1/energy/generation/group-history?station_group=zesco_diesel"));
    const chart = await screen.findByLabelText(/ZESCO DIESEL published annual total/);
    expect(chart.getAttribute("data-values")).toBe("2029:5.50,2030:6.60");
    expect(screen.getByText(/Published ERB totals, not calculated from stations/)).toBeTruthy();
  });

  it("shows group-history empty and error states without hiding station history", async () => {
    routes["/api/v1/energy/generation/group-history"] = (p) => json(ghistory(p.get("station_group")!, []));
    const { unmount } = render(<EnergyGenerationDetail state={ready()} onRefresh={() => {}} />);
    expect(await screen.findByText(/No stored published totals for ZESCO LARGE HYDRO/)).toBeTruthy();
    unmount();
    routes["/api/v1/energy/generation/group-history"] = () => json({}, 503);
    render(<EnergyGenerationDetail state={ready()} onRefresh={() => {}} />);
    expect((await screen.findAllByRole("alert"))[0].textContent).toContain("No group history is shown");
    expect(await screen.findByLabelText(/Alpha annual generation/)).toBeTruthy();
  });

  it("ignores a stale station-history response", async () => {
    const user = userEvent.setup();
    let releaseAlpha: () => void = () => {};
    routes["/api/v1/energy/generation/history"] = (p) => {
      const name = p.get("station_name")!;
      const body = history(p.get("station_group")!, name, [hpoint(2029, name === "Alpha" ? "1.11" : "2.22"), hpoint(2030, "3.00")]);
      return name === "Alpha" ? new Promise<Response>((r) => { releaseAlpha = () => r(json(body)); }) : json(body);
    };
    render(<EnergyGenerationDetail state={ready()} onRefresh={() => {}} />);
    await waitFor(() => expect(calls.some((c) => c.includes("Alpha"))).toBe(true));
    await user.click(screen.getByRole("button", { name: "Beta" }));
    expect((await screen.findByLabelText(/Beta annual generation/)).getAttribute("data-values")).toContain("2029:2.22");
    await act(async () => { releaseAlpha(); });
    expect(screen.queryByLabelText(/Alpha annual generation/)).toBeNull();
    expect(screen.getByLabelText(/Beta annual generation/)).toBeTruthy();
  });

  it("ignores a stale group-history response", async () => {
    const user = userEvent.setup();
    let release: () => void = () => {};
    routes["/api/v1/energy/generation/group-history"] = (p) => {
      const g = p.get("station_group")!;
      return g === "zesco_large_hydro" ? new Promise<Response>((r) => { release = () => r(json(ghistory(g))); }) : json(ghistory(g));
    };
    render(<EnergyGenerationDetail state={ready()} onRefresh={() => {}} />);
    await waitFor(() => expect(calls.some((c) => c.includes("group-history?station_group=zesco_large_hydro"))).toBe(true));
    await user.click(within(screen.getByRole("group", { name: "Station group" })).getByRole("button", { name: /ZESCO DIESEL|DIESEL/ }));
    await screen.findByLabelText(/ZESCO DIESEL published annual total/);
    await act(async () => { release(); });
    expect(screen.queryByLabelText(/ZESCO LARGE HYDRO published annual total/)).toBeNull();
  });

  it("keeps the selected identity across a refresh and falls back to the first station otherwise", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<EnergyGenerationDetail state={ready()} onRefresh={() => {}} />);
    await user.click(screen.getByRole("button", { name: "Beta" }));
    rerender(<EnergyGenerationDetail state={ready()} onRefresh={() => {}} />);
    expect(screen.getByRole("button", { name: "Beta" }).getAttribute("aria-pressed")).toBe("true");
    rerender(<EnergyGenerationDetail state={ready(snapshot({ stations: [station("zesco_diesel", "Only", "reported", "1.00")] }))} onRefresh={() => {}} />);
    expect(screen.getByRole("button", { name: "Only" }).getAttribute("aria-pressed")).toBe("true");
  });
});

describe("refresh, rights and empty-result regressions", () => {
  const stations2 = () => snapshot({ stations: [station("zesco_large_hydro", "Alpha", "reported", "1.00"), station("zesco_large_hydro", "Beta", "reported", "2.00")] });
  const sel = (name: string) => screen.getByRole("button", { name }).getAttribute("aria-pressed");

  it("keeps Beta selected through a real loading/refreshing transition", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<EnergyGenerationDetail state={ready(stations2())} onRefresh={() => {}} />);
    await user.click(screen.getByRole("button", { name: "Beta" }));
    expect(sel("Beta")).toBe("true");
    const prev = ready(stations2());
    rerender(<EnergyGenerationDetail state={{ ...prev, refreshing: true } as EnergyState} onRefresh={() => {}} />);
    expect(sel("Beta")).toBe("true");
    rerender(<EnergyGenerationDetail state={{ status: "loading" }} onRefresh={() => {}} />);
    rerender(<EnergyGenerationDetail state={{ status: "error", message: "x" }} onRefresh={() => {}} />);
    rerender(<EnergyGenerationDetail state={ready(stations2())} onRefresh={() => {}} />);
    expect(sel("Beta")).toBe("true");
  });
  it("falls back to the first station when the selection disappears, and clears on an empty snapshot", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<EnergyGenerationDetail state={ready(stations2())} onRefresh={() => {}} />);
    await user.click(screen.getByRole("button", { name: "Beta" }));
    rerender(<EnergyGenerationDetail state={ready(snapshot({ stations: [station("zesco_diesel", "Only", "reported", "1.00")] }))} onRefresh={() => {}} />);
    expect(sel("Only")).toBe("true");
    rerender(<EnergyGenerationDetail state={{ status: "ready", data: { snapshot: null } }} onRefresh={() => {}} />);
    rerender(<EnergyGenerationDetail state={ready(stations2())} onRefresh={() => {}} />);
    expect(sel("Alpha")).toBe("true"); // cleared by the empty snapshot, so the first station is picked again
  });
  it("uses the review heading only for review_required and a neutral one otherwise", () => {
    const { unmount } = render(<EnergyGenerationDetail state={ready()} onRefresh={() => {}} />);
    expect(screen.getByText("LOCAL / INTERNAL · RIGHTS REVIEW REQUIRED")).toBeTruthy();
    unmount();
    render(<EnergyGenerationDetail state={ready(snapshot({ rights_status: "restricted" }))} onRefresh={() => {}} />);
    expect(screen.getByText("LOCAL / INTERNAL · RIGHTS STATUS")).toBeTruthy();
    expect(screen.queryByText(/RIGHTS REVIEW REQUIRED/)).toBeNull();
    expect(screen.getAllByText(/rights_status = restricted\. Public redistribution is not represented as cleared by this interface/).length).toBeGreaterThan(0);
  });
  it("never renders null for an empty group history's rights", async () => {
    routes["/api/v1/energy/generation/group-history"] = (p) => json({ ...ghistory(p.get("station_group")!, []), rights_status: null });
    render(<EnergyGenerationDetail state={ready()} onRefresh={() => {}} />);
    expect(await screen.findByText(/No publication rights state is available for this empty result/)).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/null|undefined/);
  });
});

describe("long station names", () => {
  it("puts the wrapping class on the station-history heading and keeps the full exact name", async () => {
    const long = "X".repeat(96);
    render(<EnergyGenerationDetail state={ready(snapshot({ stations: [station("zesco_diesel", long, "reported", "1.00")] }))} onRefresh={() => {}} />);
    const title = within(screen.getByLabelText("Station annual history")).getByText(new RegExp(`STATION HISTORY · ${long}`));
    expect(title.className).toContain("energy-history-title");
    expect(title.textContent).toContain(long);
    await screen.findByLabelText(new RegExp(`${long} annual generation`));
  });
});

describe("Energy in the app", () => {
  const appRoutes = () => {
    routes["/api/v1/energy/generation/latest"] = () => json({ snapshot: snapshot() });
  };

  it("shows the dashboard card with the observation year and counts, no derived total", async () => {
    appRoutes();
    render(<App />);
    const card = (await screen.findByText("POWER ZM", { selector: ".market-label" })).closest("button")!;
    await waitFor(() => expect(within(card).getByText("2030")).toBeTruthy());
    expect(card.textContent).toContain("4 STATIONS · 4 PUBLISHED GROUP TOTALS");
    expect(card.textContent).not.toMatch(/GWh/);
    expect(card.textContent).toContain("rights review_required");
  });

  it("opens the detail from the card and from the POWER ZM command", async () => {
    appRoutes();
    const user = userEvent.setup();
    render(<App />);
    await user.click((await screen.findByText("POWER ZM", { selector: ".market-label" })).closest("button")!);
    expect(await screen.findByLabelText("Electricity generation detail")).toBeTruthy();
    cleanup();
    render(<App />);
    await user.type(screen.getByLabelText("Terminal command"), "power zm{Enter}");
    expect(await screen.findByLabelText("Electricity generation detail")).toBeTruthy();
  });

  it("card shows error and empty states", async () => {
    routes["/api/v1/energy/generation/latest"] = () => json({}, 503);
    const { unmount } = render(<App />);
    expect(await screen.findByText("Stored Energy data unavailable")).toBeTruthy();
    unmount();
    routes["/api/v1/energy/generation/latest"] = () => json({ snapshot: null });
    render(<App />);
    expect(await screen.findByText("No stored ERB generation")).toBeTruthy();
  });
});
