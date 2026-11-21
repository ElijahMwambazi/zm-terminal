export type AnnualPoint = { year: number; value: string | null };
export type ReportedRun = { year: number; value: string }[];

/** Splits an annual series into contiguous runs of reported values. A run ends at a not_reported year AND at any
 * non-consecutive year (a year absent from the history), so nothing is ever drawn across missing information.
 * A reported "0.00" is a real value and never breaks a run. Calendar years are never inferred or filled. */
export function splitEnergyReportedRuns(points: AnnualPoint[]): ReportedRun[] {
  const runs: ReportedRun[] = [];
  let current: ReportedRun | null = null;
  for (const p of points) {
    if (p.value === null) { current = null; continue; }
    const last: { year: number } | undefined = current?.[current.length - 1];
    if (current && last && p.year === last.year + 1) current.push({ year: p.year, value: p.value });
    else { current = [{ year: p.year, value: p.value }]; runs.push(current); }
  }
  return runs;
}
