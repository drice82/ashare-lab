import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  LineSeries,
  createChart,
  type Time,
} from "lightweight-charts";
import { formatMoney, performanceColor, performanceColors } from "../format";
import type { Bar } from "../types";

export default function PriceChart({ bars }: { bars: Bar[] }) {
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!container.current || !bars.length) return;
    const chart = createChart(container.current, {
      width: container.current.clientWidth,
      height: 450,
      layout: {
        background: { type: ColorType.Solid, color: "#ffffff" },
        textColor: "#748075",
        fontFamily: "system-ui",
      },
      grid: {
        vertLines: { color: "#f0f3ed" },
        horzLines: { color: "#f0f3ed" },
      },
      localization: { locale: "zh-CN" },
      timeScale: { borderColor: "#e0e6df" },
      rightPriceScale: { borderColor: "#e0e6df" },
    });
    const candle = chart.addSeries(CandlestickSeries, {
      upColor: performanceColors.positive,
      downColor: performanceColors.negative,
      borderVisible: false,
      priceFormat: {
        type: "custom",
        formatter: (value: number) => formatMoney(value, ""),
      },
      wickUpColor: performanceColors.positive,
      wickDownColor: performanceColors.negative,
    });
    candle.setData(
      bars.map((bar) => ({
        time: bar.trade_date as Time,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
        color: performanceColor(bar.close - bar.open),
        wickColor: performanceColor(bar.close - bar.open),
      })),
    );
    for (const [field, color] of [
      ["ma20", "#bd933d"],
      ["ma60", "#788db0"],
    ] as const) {
      const series = chart.addSeries(LineSeries, {
        color,
        lineWidth: 1,
        title: field.toUpperCase(),
        priceFormat: {
          type: "custom",
          formatter: (value: number) => formatMoney(value, ""),
        },
        priceLineVisible: false,
        lastValueVisible: false,
      });
      series.setData(
        bars.map((bar) =>
          bar[field] === null
            ? { time: bar.trade_date as Time }
            : { time: bar.trade_date as Time, value: bar[field] },
        ),
      );
    }
    const volume = chart.addSeries(
      HistogramSeries,
      { priceFormat: { type: "volume" }, priceLineVisible: false },
      1,
    );
    volume.setData(
      bars
        .filter((bar) => bar.volume !== null)
        .map((bar) => ({
          time: bar.trade_date as Time,
          value: bar.volume!,
          color: `${performanceColor(bar.close - bar.open)}66`,
        })),
    );
    chart.panes()[1]?.setHeight(110);
    chart.timeScale().fitContent();
    const observer = new ResizeObserver((entries) => {
      if (entries[0])
        chart.applyOptions({ width: entries[0].contentRect.width });
    });
    observer.observe(container.current);
    return () => {
      observer.disconnect();
      chart.remove();
    };
  }, [bars]);
  return (
    <div
      ref={container}
      aria-label="日K线、MA20、MA60及成交量图"
      className="min-h-[450px] w-full"
    />
  );
}
