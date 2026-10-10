import { useEffect, useRef } from "react";
import {
  ColorType,
  LineSeries,
  createChart,
  type Time,
} from "lightweight-charts";
import { formatMoney, performanceColor, performanceColors } from "../format";
import type { DcaPoint } from "../types";

export default function DcaChart({ points }: { points: DcaPoint[] }) {
  const element = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!element.current || !points.length) return;
    const chart = createChart(element.current, {
      width: element.current.clientWidth,
      height: 320,
      layout: {
        background: { type: ColorType.Solid, color: "#ffffff" },
        textColor: "#748075",
      },
      localization: { locale: "zh-CN" },
    });
    for (const [field, title, color] of [
      [
        "total_assets",
        "总资产",
        performanceColor(
          points.at(-1)!.total_assets - points.at(-1)!.contributed,
        ),
      ],
      ["contributed", "累计投入", performanceColors.neutral],
    ] as const) {
      const series = chart.addSeries(LineSeries, {
        title,
        color,
        priceLineVisible: false,
        priceFormat: {
          type: "custom",
          formatter: (value: number) => formatMoney(value, ""),
        },
      });
      series.setData(
        points.map((p) => ({
          time: p.date as Time,
          value: p[field],
          color:
            field === "total_assets"
              ? performanceColor(p.total_assets - p.contributed)
              : performanceColors.neutral,
        })),
      );
    }
    chart.timeScale().fitContent();
    const observer = new ResizeObserver((entries) => {
      if (entries[0])
        chart.applyOptions({ width: entries[0].contentRect.width });
    });
    observer.observe(element.current);
    return () => {
      observer.disconnect();
      chart.remove();
    };
  }, [points]);
  return (
    <div
      ref={element}
      className="min-h-[320px] w-full"
      aria-label="总资产与累计投入曲线"
    />
  );
}
