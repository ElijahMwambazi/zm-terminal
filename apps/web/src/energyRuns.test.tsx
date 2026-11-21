import { describe, expect, it } from "vitest";
import { splitEnergyReportedRuns } from "./energyRuns";

const p = (year: number, value: string | null) => ({ year, value });
const years = (runs: { year: number }[][]) => runs.map((r) => r.map((x) => x.year));

describe("splitEnergyReportedRuns", () => {
  it("keeps consecutive reported years in one run", () => expect(years(splitEnergyReportedRuns([p(2018, "1.00"), p(2019, "2.00")]))).toEqual([[2018, 2019]]));
  it("splits at a not_reported year", () =>
    expect(years(splitEnergyReportedRuns([p(2018, "1.00"), p(2019, "1.00"), p(2020, null), p(2021, "1.00"), p(2022, "1.00")]))).toEqual([[2018, 2019], [2021, 2022]]));
  it("keeps consecutive missing years as a break", () =>
    expect(years(splitEnergyReportedRuns([p(2018, "1.00"), p(2019, null), p(2020, null), p(2021, "1.00")]))).toEqual([[2018], [2021]]));
  it("splits at a year absent from the history", () =>
    expect(years(splitEnergyReportedRuns([p(2019, "1.00"), p(2021, "1.00")]))).toEqual([[2019], [2021]]));
  it("does not break at a reported 0.00", () => {
    const runs = splitEnergyReportedRuns([p(2019, "1.00"), p(2020, "0.00"), p(2021, "1.00")]);
    expect(years(runs)).toEqual([[2019, 2020, 2021]]);
    expect(runs[0][1].value).toBe("0.00");
  });
  it("returns no runs when everything is not reported", () => expect(splitEnergyReportedRuns([p(2019, null), p(2020, null)])).toEqual([]));
  it("returns a singleton run for one reported point", () => expect(years(splitEnergyReportedRuns([p(2019, null), p(2020, "5.00")]))).toEqual([[2020]]));
});
