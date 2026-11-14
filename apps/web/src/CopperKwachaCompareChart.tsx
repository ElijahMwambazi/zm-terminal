import { ColorType, LineSeries, createChart, type Time } from "lightweight-charts";
import { useEffect, useRef } from "react";

export type CompareSeries = { key: string; label: string; color: string; values: number[] };

const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Lines of values already rebased to 100 at the first matched month. `months` are first-of-month dates (YYYY-MM-DD), ascending.
 * Months without both inputs are simply absent from the data: the line only joins matched months and nothing is filled. */
export default function CopperKwachaCompareChart({ months, series, label }: { months: string[]; series: CompareSeries[]; label: string }) {
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const chart = createChart(element, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#8c8c8c", attributionLogo: true },
      grid: { vertLines: { color: "#1c1c1c" }, horzLines: { color: "#1c1c1c" } },
      rightPriceScale: { borderColor: "#292929" },
      timeScale: { borderColor: "#292929", timeVisible: false },
      localization: {
        priceFormatter: (value: number) => value.toFixed(1),
        timeFormatter: (t: Time) => (typeof t === "object" && "month" in t ? `${MONTH_NAMES[t.month - 1]} ${t.year}` : String(t)),
      },
    });
    for (const item of series) {
      const line = chart.addSeries(LineSeries, {
        color: item.color, lineWidth: 2, pointMarkersVisible: months.length <= 36, pointMarkersRadius: 3,
        priceFormat: { type: "price", precision: 1, minMove: 0.1 }, title: "",
      });
      line.setData(months.map((month, i) => ({ time: month as Time, value: item.values[i] })));
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [months, series]);

  return <div ref={container} className="price-chart" role="img" aria-label={label} />;
}
