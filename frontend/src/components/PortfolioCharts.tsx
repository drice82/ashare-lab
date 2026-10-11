import { useEffect, useRef } from "react";
import {
  createChart,
  LineSeries,
  ColorType,
  type Time,
} from "lightweight-charts";
import type { PortfolioResults } from "../portfolio";
import { formatPercent } from "../format";
export default function PortfolioCharts({
  result,
  field,
}: {
  result: PortfolioResults;
  field: "return_rate" | "drawdown";
}) {
  const host = useRef<HTMLDivElement>(null);
  const tooltip = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (!host.current) return;
    const chart = createChart(host.current, {
      height: 300,
      width: host.current.clientWidth,
      layout: {
        background: { type: ColorType.Solid, color: "white" },
        textColor: "#475569",
      },
      localization: { locale: "zh-CN" },
    });
    const entries = [
      { label: "实际组合", color: "#2563eb", account: result },
      { label: "买入持有基准", color: "#64748b", account: result.benchmark },
    ];
    for (const { label, color, account } of entries) {
      const line = chart.addSeries(LineSeries, {
        title: label + (account.summary.complete ? "" : "（部分估算）"),
        color,
        lineStyle: account.summary.complete ? 0 : 2,
        priceLineVisible: false,
        priceFormat: { type: "custom", formatter: formatPercent },
      });
      line.setData(
        account.curve.map((point) => ({
          time: point.date as Time,
          value: point[field],
        })),
      );
    }
    chart.subscribeCrosshairMove((event) => {
      if (tooltip.current)
        tooltip.current.textContent = event.time
          ? String(event.time) +
            " · " +
            entries
              .map(
                ({ label, account }) =>
                  label +
                  " " +
                  formatPercent(
                    account.curve.find((p) => p.date === String(event.time))?.[
                      field
                    ],
                  ),
              )
              .join(" · ")
          : "移动到曲线上查看同日组合与基准数值";
    });
    chart.timeScale().fitContent();
    const observer = new ResizeObserver((entries) => {
      if (entries[0].contentRect.width > 0)
        chart.applyOptions({ width: entries[0].contentRect.width });
    });
    observer.observe(host.current);
    return () => {
      observer.disconnect();
      chart.remove();
    };
  }, [result, field]);
  return (
    <div className="min-w-0">
      <div
        ref={host}
        role="img"
        aria-label={
          field === "return_rate" ? "组合累计收益率曲线" : "组合回撤曲线"
        }
        className="min-h-[300px] w-full overflow-hidden"
      />
      <p ref={tooltip} className="mt-2 text-xs text-muted-foreground">
        移动到曲线上查看同日组合与基准数值
      </p>
    </div>
  );
}
