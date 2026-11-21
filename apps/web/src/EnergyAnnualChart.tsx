import { ColorType, LineSeries, createChart, type Time } from "lightweight-charts";
import { useEffect, useRef } from "react";

export type AnnualPoint = { year: number; value: string | null };

/** Annual GWh line. A null value (not_reported) becomes a whitespace point, so the line breaks: nothing is interpolated,
 * filled forward or drawn as zero. Numbers exist only for drawing; the exact strings are shown in the adjacent table. */
export default function EnergyAnnualChart({ points, label, color = "#ffb000" }: { points: AnnualPoint[]; label: string; color?: string }) {
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const chart = createChart(element, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#8c8c8c", attributionLogo: true },
      grid: { vertLines: { color: "#1c1c1c" }, horzLines: { color: "#1c1c1c" } },
      rightPriceScale: { borderColor: "#292929" },
      timeScale: { borderColor: "#292929", timeVisible: false, minBarSpacing: 8 },
      localization: {
        priceFormatter: (v: number) => v.toLocaleString("en-US", { maximumFractionDigits: 2 }),
        timeFormatter: (t: Time) => String(t).slice(0, 4),
      },
    });
    const line = chart.addSeries(LineSeries, { color, lineWidth: 2, pointMarkersVisible: true, pointMarkersRadius: 3, lastValueVisible: false, priceLineVisible: false });
    line.setData(points.map((p) => {
      const time = `${p.year}-01-01` as Time;
      return p.value === null ? { time } : { time, value: Number(p.value) };
    }));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [points, color]);
  return <div ref={container} className="price-chart energy-chart" role="img" aria-label={label} />;
}
