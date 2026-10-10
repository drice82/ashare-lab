import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  LineSeries,
  createChart,
  createSeriesMarkers,
  type Time,
} from "lightweight-charts";
import {
  strategies,
  type StrategyDetail,
  type StrategyResult,
} from "../comparison";
import { formatMoney, formatPercent } from "../format";

export function ComparisonChart({
  results,
  field,
}: {
  results: StrategyResult[];
  field: "return_rate" | "drawdown";
}) {
  const host = useRef<HTMLDivElement>(null);
  const tooltip = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (!host.current) return;
    const chart = createChart(host.current, {
      height: 320,
      width: host.current.clientWidth,
      layout: {
        background: { type: ColorType.Solid, color: "#ffffff" },
        textColor: "#475569",
      },
      localization: { locale: "zh-CN" },
    });
    const series = results.map((result) => {
      const line = chart.addSeries(LineSeries, {
        title: result.label + (!result.summary.complete ? "（部分估算）" : ""),
        color: strategies.find((item) => item.id === result.strategy_id)!.color,
        lineStyle: result.summary.complete ? 0 : 2,
        priceLineVisible: false,
        priceFormat: {
          type: "custom",
          formatter: (value: number) => formatPercent(value),
        },
      });
      line.setData(
        result.curve.map((point) => ({
          time: point.date as Time,
          value: point[field],
        })),
      );
      return { line, result };
    });
    chart.subscribeCrosshairMove((event) => {
      if (!tooltip.current) return;
      tooltip.current.textContent = event.time
        ? `${String(event.time)} · ` +
          series
            .map(({ result }) => {
              const point = result.curve.find(
                (item) => item.date === String(event.time),
              );
              return `${result.label} ${point ? formatPercent(point[field]) : "无数据"}`;
            })
            .join(" · ")
        : "移动到曲线上查看同日各策略数值";
    });
    chart.timeScale().fitContent();
    const observer = new ResizeObserver((entries) => {
      const width = entries[0].contentRect.width;
      if (width > 0) chart.applyOptions({ width });
    });
    observer.observe(host.current);
    return () => {
      observer.disconnect();
      chart.remove();
    };
  }, [results, field]);
  return (
    <>
      <div
        ref={host}
        className="min-h-[320px] w-full"
        role="img"
        aria-label={
          field === "return_rate" ? "多策略累计收益率曲线" : "多策略回撤曲线"
        }
      />
      <p ref={tooltip} className="mt-2 min-h-8 text-xs text-muted-foreground">
        移动到曲线上查看同日各策略数值
      </p>
    </>
  );
}

export function TradeChart({ detail }: { detail: StrategyDetail }) {
  const host = useRef<HTMLDivElement>(null);
  const tooltip = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (!host.current) return;
    const chart = createChart(host.current, {
      height: 360,
      width: host.current.clientWidth,
      layout: {
        background: { type: ColorType.Solid, color: "#ffffff" },
        textColor: "#475569",
      },
      localization: { locale: "zh-CN" },
    });
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: "#dc2626",
      downColor: "#16a34a",
      wickUpColor: "#dc2626",
      wickDownColor: "#16a34a",
      borderVisible: false,
    });
    candles.setData(
      detail.prices.flatMap((point) =>
        point.open != null &&
        point.high != null &&
        point.low != null &&
        point.close != null &&
        [point.open, point.high, point.low, point.close].every(
          (value) => Number.isFinite(value) && value > 0,
        )
          ? [
              {
                time: point.date as Time,
                open: point.open,
                high: point.high,
                low: point.low,
                close: point.close,
              },
            ]
          : [],
      ),
    );
    createSeriesMarkers(
      candles,
      detail.trades.map((row) => ({
        time: row.date as Time,
        position:
          row.event === "buy" ? ("belowBar" as const) : ("aboveBar" as const),
        shape:
          row.event === "buy" ? ("arrowUp" as const) : ("arrowDown" as const),
        color: row.event === "buy" ? "#dc2626" : "#16a34a",
        text: row.event === "buy" ? "买" : "卖",
      })),
    );
    chart.subscribeCrosshairMove((event) => {
      if (!tooltip.current) return;
      const trades = detail.trades.filter(
        (row) => row.date === String(event.time),
      );
      tooltip.current.textContent = event.time
        ? `${String(event.time)} · ` +
          (trades.length
            ? trades
                .map(
                  (row) =>
                    `${row.event === "buy" ? "买入" : "卖出"} ${Math.abs(row.shares_delta)}股 @ ${formatMoney(row.price)}元；信号日 ${row.signal_date}；费用 ${formatMoney(row.fee)}元；分红税 ${formatMoney(row.tax)}元`,
                )
                .join(" / ")
            : "无成交")
        : "移动到成交日查看信号日、成交价、股数和费用";
    });
    chart.timeScale().fitContent();
    const observer = new ResizeObserver((entries) => {
      const width = entries[0].contentRect.width;
      if (width > 0) chart.applyOptions({ width });
    });
    observer.observe(host.current);
    return () => {
      observer.disconnect();
      chart.remove();
    };
  }, [detail]);
  return (
    <>
      <div
        ref={host}
        className="min-h-[360px] w-full"
        role="img"
        aria-label={`${detail.label}股价与买卖点`}
      />
      <p ref={tooltip} className="mt-2 min-h-8 text-xs text-muted-foreground">
        移动到成交日查看信号日、成交价、股数和费用
      </p>
    </>
  );
}

export function MetricBars({
  results,
  field,
}: {
  results: StrategyResult[];
  field: "return_rate" | "max_drawdown";
}) {
  const values = results.filter(
    (item) => item.summary.complete && item.summary[field] != null,
  );
  const maximum = Math.max(
    0.01,
    ...values.map((item) => Math.abs(item.summary[field]!)),
  );
  return (
    <svg
      role="img"
      aria-label={
        field === "return_rate" ? "累计收益率对比柱状图" : "最大回撤对比柱状图"
      }
      viewBox={`0 0 440 ${Math.max(50, values.length * 42 + 20)}`}
      className="w-full max-w-[440px]"
    >
      <line
        x1="250"
        x2="250"
        y1="0"
        y2={values.length * 42 + 10}
        stroke="#cbd5e1"
      />
      {values.map((item, index) => {
        const value = item.summary[field]!;
        const width = (Math.abs(value) / maximum) * 100;
        return (
          <g key={item.strategy_id}>
            <title>
              {item.label}：{formatPercent(value)}
            </title>
            <text x="0" y={index * 42 + 25} fontSize="12">
              {item.label}
            </text>
            <rect
              x={value < 0 ? 250 - width : 250}
              y={index * 42 + 12}
              width={width}
              height="16"
              rx="3"
              fill={
                strategies.find((strategy) => strategy.id === item.strategy_id)!
                  .color
              }
            />
            <text x="355" y={index * 42 + 25} fontSize="12">
              {formatPercent(value)}
            </text>
          </g>
        );
      })}
      {!values.length && (
        <text x="0" y="30" fontSize="16">
          暂无完整结果
        </text>
      )}
    </svg>
  );
}
