import { ColorType, LineSeries, createChart, type Time } from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { AuctionPoint } from "./api";

/** One marker per real auction (calendar dates, no time zone shift). Nothing is drawn between auctions
 * except the connecting line segment; no value is interpolated or filled in. */
type Props = {
  points: Pick<AuctionPoint, "auction_date" | "yield_rate">[];
  label: string;
  precision?: number;
  /** Axis/tooltip price text, e.g. (v) => `K${v.toFixed(2)}`. */
  formatPrice?: (value: number) => string;
  /** Show crosshair dates as months (monthly series). */
  monthly?: boolean;
};

const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export default function AuctionChart({ points, label, precision = 4, formatPrice, monthly }: Props) {
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
        ...(formatPrice ? { priceFormatter: formatPrice } : {}),
        ...(monthly ? { timeFormatter: (t: Time) => (typeof t === "object" && "month" in t ? `${MONTH_NAMES[t.month - 1]} ${t.year}` : String(t)) } : {}),
      },
    });
    const series = chart.addSeries(LineSeries, {
      color: "#ffb000", lineWidth: 1, pointMarkersVisible: true, pointMarkersRadius: 4,
      priceFormat: { type: "price", precision, minMove: 10 ** -precision },
    });
    let last = "";
    series.setData(
      points.flatMap((point) => {
        if (point.auction_date <= last) return []; // API order is ascending; one point per date
        last = point.auction_date;
        return [{ time: point.auction_date as Time, value: Number(point.yield_rate) }];
      }),
    );
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [points, precision, formatPrice, monthly]);

  return <div ref={container} className="price-chart" role="img" aria-label={label} />;
}
