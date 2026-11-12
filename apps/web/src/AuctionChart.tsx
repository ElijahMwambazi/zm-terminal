import { ColorType, LineSeries, createChart, type Time } from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { AuctionPoint } from "./api";

/** One marker per real auction (calendar dates, no time zone shift). Nothing is drawn between auctions
 * except the connecting line segment; no value is interpolated or filled in. */
export default function AuctionChart({ points, label }: { points: AuctionPoint[]; label: string }) {
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
    });
    const series = chart.addSeries(LineSeries, {
      color: "#ffb000", lineWidth: 1, pointMarkersVisible: true, pointMarkersRadius: 4,
      priceFormat: { type: "price", precision: 4, minMove: 0.0001 },
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
  }, [points]);

  return <div ref={container} className="price-chart" role="img" aria-label={label} />;
}
