import { ColorType, LineSeries, createChart, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { FxHistoryPoint } from "./api";

const tz = (options: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat("en-GB", { timeZone: "Africa/Lusaka", hour12: false, ...options });
const axisDay = tz({ day: "2-digit", month: "short" });
const axisTime = tz({ hour: "2-digit", minute: "2-digit" });
const crosshair = tz({ day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });

/** Plots `mid` only. Numbers are for drawing; exact decimals stay in the data. */
export default function PriceChart({ points }: { points: FxHistoryPoint[] }) {
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const chart = createChart(element, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#8c8c8c", attributionLogo: true },
      grid: { vertLines: { color: "#1c1c1c" }, horzLines: { color: "#1c1c1c" } },
      rightPriceScale: { borderColor: "#292929" },
      timeScale: {
        borderColor: "#292929",
        timeVisible: true,
        tickMarkFormatter: (time: number) => {
          const date = new Date(time * 1000);
          return axisDay.format(date) + (axisTime.format(date) === "00:00" ? "" : " " + axisTime.format(date));
        },
      },
      localization: { timeFormatter: (time: number) => `${crosshair.format(new Date(time * 1000))} CAT` },
    });
    const series = chart.addSeries(LineSeries, { color: "#ffb000", lineWidth: 2, priceFormat: { type: "price", precision: 4, minMove: 0.0001 } });
    let lastTime = 0;
    series.setData(
      points.flatMap((point) => {
        const time = Math.floor(Date.parse(point.observed_at) / 1000);
        if (!Number.isFinite(time) || time <= lastTime) return []; // API order is ascending; guard anyway
        lastTime = time;
        return [{ time: time as UTCTimestamp, value: Number(point.mid) }];
      }),
    );
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [points]);

  return <div ref={container} className="price-chart" role="img" aria-label="USD/ZMW mid rate history" />;
}
