import { directionLabels, strategyName } from "../screeningSummary";
import { parameterLabels } from "../comparison";
import type { SignalResult } from "../types";

const evidenceLabels: Record<string, string> = {
  adjusted_close: "复权收盘价",
  window_start: "预热起始日",
  warmup_days: "预热交易日数",
  previous_short: "前日短均线",
  previous_long: "前日长均线",
  current_short: "当日短均线",
  current_long: "当日长均线",
  previous_dif: "前日 DIF",
  previous_dea: "前日 DEA",
  current_dif: "当日 DIF",
  current_dea: "当日 DEA",
  previous_rsi: "前日 RSI",
  current_rsi: "当日 RSI",
  lower: "下阈值",
  upper: "上阈值",
  previous_high: "此前窗口最高价",
  previous_low: "此前窗口最低价",
  previous_middle: "前日中轨",
  previous_lower: "前日下轨",
  current_middle: "当日中轨",
  current_lower: "当日下轨",
};

export default function SignalResults({
  results,
}: {
  results: SignalResult[];
}) {
  return (
    <section className="mt-5 space-y-3" aria-label="策略信号详情">
      <h3 className="font-medium">策略信号 · 收盘判断</h3>
      {results.map((result) => (
        <div className="rounded-lg border p-3" key={result.strategy_id}>
          <p className="font-medium">
            {strategyName(result.strategy_id)} · {result.trade_date} ·{" "}
            {result.direction
              ? directionLabels[result.direction]
              : result.status === "insufficient_data"
                ? "数据不足"
                : "未触发"}
            <span className="badge ml-2">
              {result.matched === null
                ? "数据不足"
                : result.matched
                  ? "满足筛选方向"
                  : "不满足筛选方向"}
            </span>
          </p>
          <p className="mt-1 text-sm text-muted-foreground">{result.reason}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {Object.entries(result.parameters)
              .filter(([key]) => key !== "id")
              .map(([key, value]) => `${parameterLabels[key] ?? key} ${value}`)
              .join(" / ")}
          </p>
          <dl className="mt-2 grid gap-2 text-sm sm:grid-cols-2">
            {Object.entries(result.evidence ?? {}).map(([key, value]) => (
              <div key={key} className="flex min-w-0 flex-wrap gap-2">
                <dt className="text-muted-foreground">
                  {evidenceLabels[key] ?? key}
                </dt>
                <dd>
                  {typeof value === "number"
                    ? Number(value.toPrecision(12)).toString()
                    : value}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      ))}
      <p className="text-xs text-muted-foreground">
        信号不代表成交；卖出信号不要求当前持有该股票。
      </p>
    </section>
  );
}
