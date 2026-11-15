import { ColorType, LineSeries, LineStyle, createChart, type IChartApi, type ISeriesApi, type Time } from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { FxMonthlyPoint } from "./api";
import { formatYearMonth } from "./format";

export type RateKey = "buy" | "mid" | "sell";
export const RATE_STYLE: Record<RateKey, { label: string; color: string; style: LineStyle }> = {
  mid: { label: "Mid", color: "#ffb000", style: LineStyle.Solid },
  buy: { label: "Buy", color: "#7cc4ff", style: LineStyle.Dashed },
  sell: { label: "Sell", color: "#ff7a6b", style: LineStyle.Dotted },
};

const toMonth = (time: Time | undefined): string | null => {
  if (!time) return null;
  if (typeof time === "string") return time.slice(0, 7);
  if (typeof time === "object" && "month" in time) return `${time.year}-${String(time.month).padStart(2, "0")}`;
  return null;
};

type Props = {
  observations: FxMonthlyPoint[];
  active: RateKey[];
  selectedMonth: string | null;
  onInspect: (month: string) => void;
};

/** One marker per published month; the line only joins existing observations (no smoothing, nothing filled in).
 * Numbers are for drawing only: exact decimal strings stay in `observations`. */
export default function MonthlyFxChart({ observations, active, selectedMonth, onInspect }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const chartRef = useRef<{ chart: IChartApi; series: ISeriesApi<"Line">; key: RateKey } | null>(null);
  const inspect = useRef(onInspect);
  inspect.current = onInspect;

  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const chart = createChart(element, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#8c8c8c", attributionLogo: true },
      grid: { vertLines: { color: "#1c1c1c" }, horzLines: { color: "#1c1c1c" } },
      rightPriceScale: { borderColor: "#292929" },
      timeScale: { borderColor: "#292929", timeVisible: false, minBarSpacing: 0.5 },
      localization: {
        priceFormatter: (value: number) => value.toFixed(2),
        timeFormatter: (t: Time) => { const m = toMonth(t); return m ? formatYearMonth(m) : String(t); },
      },
    });
    const keys = active.length ? active : (["mid"] as RateKey[]);
    const lines = keys.map((key) => {
      const { color, style } = RATE_STYLE[key];
      const line = chart.addSeries(LineSeries, {
        color, lineStyle: style, lineWidth: 2, pointMarkersVisible: observations.length <= 60, pointMarkersRadius: 3,
        priceFormat: { type: "price", precision: 2, minMove: 0.01 }, title: "", lastValueVisible: false, priceLineVisible: false,
      });
      line.setData(observations.map((o) => ({ time: `${o.month}-01` as Time, value: Number(o[key]) })));
      return { key, line };
    });
    chartRef.current = { chart, series: lines[0].line, key: lines[0].key };
    chart.timeScale().fitContent();
    const handler = (param: { time?: Time }) => { const m = toMonth(param.time); if (m) inspect.current(m); };
    chart.subscribeCrosshairMove(handler);
    chart.subscribeClick(handler);
    return () => { chartRef.current = null; chart.remove(); };
  }, [observations, active]);

  // Keep the crosshair on the month shown in the readout (prev/next buttons, table selection).
  useEffect(() => {
    const current = chartRef.current;
    const point = observations.find((o) => o.month === selectedMonth);
    if (!current || !point) return;
    try { current.chart.setCrosshairPosition(Number(point[current.key]), `${point.month}-01` as Time, current.series); } catch { /* cosmetic */ }
  }, [observations, active, selectedMonth]);

  return <div ref={container} className="price-chart monthly-chart" />;
}
