import { ColorType, LineSeries, createChart, type Time } from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";
import { splitEnergyReportedRuns, type AnnualPoint } from "./energyRuns";

export type { AnnualPoint };

const yearOf = (time: Time | undefined): number | null => {
  if (time === undefined) return null;
  if (typeof time === "string") return Number(time.slice(0, 4));
  if (typeof time === "object" && "year" in time) return time.year;
  return null;
};

/** Annual GWh chart.
 * STRUCTURAL INVARIANT: one Lightweight Charts line series per contiguous reported run (see splitEnergyReportedRuns).
 * A series therefore never holds values from both sides of a not_reported or absent year, so no line can cross one.
 * A one-point run is drawn as a marker only (lineVisible: false), never as a line.
 * Numbers are used ONLY as plotting coordinates and approximate axis ticks. Every user-facing value (the readout below)
 * is the original API string, looked up by year; no value is ever rebuilt from a chart number. */
export default function EnergyAnnualChart({ points, label, color = "#ffb000" }: { points: AnnualPoint[]; label: string; color?: string }) {
  const container = useRef<HTMLDivElement>(null);
  const runs = useMemo(() => splitEnergyReportedRuns(points), [points]);
  const byYear = useMemo(() => new Map(points.filter((p) => p.value !== null).map((p) => [p.year, p.value as string])), [points]);
  const lastYear = useMemo(() => [...byYear.keys()].at(-1) ?? null, [byYear]);
  const [picked, setPicked] = useState<number | null>(null);
  const shown = picked !== null && byYear.has(picked) ? picked : lastYear;

  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const chart = createChart(element, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#8c8c8c", attributionLogo: true },
      grid: { vertLines: { color: "#1c1c1c" }, horzLines: { color: "#1c1c1c" } },
      rightPriceScale: { borderColor: "#292929" },
      timeScale: { borderColor: "#292929", timeVisible: false, minBarSpacing: 8 },
      // Axis ticks only: approximate coordinate labels, not published values.
      localization: { priceFormatter: (v: number) => v.toLocaleString("en-US", { maximumFractionDigits: 2 }), timeFormatter: (t: Time) => String(yearOf(t) ?? "") },
    });
    for (const run of runs) {
      const series = chart.addSeries(LineSeries, {
        color, lineWidth: 2, lineVisible: run.length > 1, pointMarkersVisible: true, pointMarkersRadius: 3,
        lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: true,
      });
      series.setData(run.map((p) => ({ time: `${p.year}-01-01` as Time, value: Number(p.value) })));
    }
    chart.timeScale().fitContent();
    const handler = (param: { time?: Time }) => { const y = yearOf(param.time); if (y !== null) setPicked(y); };
    chart.subscribeCrosshairMove(handler);
    chart.subscribeClick(handler);
    return () => chart.remove();
  }, [runs, color]);

  return (
    <>
      <div ref={container} className="price-chart energy-chart" role="img" aria-label={label} data-runs={runs.length} />
      <p className="notice energy-readout" aria-live="polite">
        {shown !== null && byYear.has(shown) ? `${shown} · ${byYear.get(shown)} GWh` : "—"}
        <span className="muted"> (published value; hover or tap a point)</span>
      </p>
    </>
  );
}
