import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const created: { options: Record<string, unknown>; data: { time: string; value: number }[] }[] = [];
let crosshair: (p: { time?: string }) => void = () => {};
vi.mock("lightweight-charts", () => ({
  ColorType: { Solid: "solid" }, LineSeries: "line",
  createChart: () => ({
    addSeries: (_t: unknown, options: Record<string, unknown>) => {
      const rec = { options, data: [] as { time: string; value: number }[] };
      created.push(rec);
      return { setData: (d: { time: string; value: number }[]) => { rec.data = d; } };
    },
    timeScale: () => ({ fitContent: () => {} }),
    subscribeCrosshairMove: (h: typeof crosshair) => { crosshair = h; },
    subscribeClick: () => {},
    remove: () => {},
  }),
}));
import { act } from "@testing-library/react";
import EnergyAnnualChart from "./EnergyAnnualChart";

afterEach(() => { cleanup(); created.length = 0; });
const p = (year: number, value: string | null) => ({ year, value });

describe("EnergyAnnualChart", () => {
  it("uses one series per contiguous reported run, so no series spans a not_reported or absent year", () => {
    render(<EnergyAnnualChart label="x" points={[p(2018, "1.00"), p(2019, "2.00"), p(2020, null), p(2021, "3.00"), p(2022, "4.00"), p(2024, "5.00")]} />);
    const spans = created.map((s) => s.data.map((d) => Number(d.time.slice(0, 4))));
    expect(spans).toEqual([[2018, 2019], [2021, 2022], [2024]]);
    for (const s of spans) for (let i = 1; i < s.length; i++) expect(s[i]).toBe(s[i - 1] + 1);
  });
  it("draws a singleton run as a marker only, and keeps a reported zero in its run", () => {
    render(<EnergyAnnualChart label="x" points={[p(2019, "1.00"), p(2020, "0.00"), p(2021, null), p(2022, "2.00")]} />);
    expect(created).toHaveLength(2);
    expect(created[0].options.lineVisible).toBe(true);
    expect(created[0].data.map((d) => d.value)).toEqual([1, 0]);
    expect(created[1].options.lineVisible).toBe(false);
  });
  it("reads out the original strings, never a number-derived one", () => {
    render(<EnergyAnnualChart label="x" points={[p(2019, "393.40"), p(2020, "0.00"), p(2021, "7.10")]} />);
    expect(screen.getByText(/2021 · 7\.10 GWh/)).toBeTruthy(); // default: newest reported
    act(() => crosshair({ time: "2019-01-01" }));
    expect(screen.getByText(/2019 · 393\.40 GWh/)).toBeTruthy();
    act(() => crosshair({ time: "2020-01-01" }));
    expect(screen.getByText(/2020 · 0\.00 GWh/)).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/393\.4 GWh|2020 · 0 GWh/);
  });
});
