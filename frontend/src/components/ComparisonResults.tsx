import StockIdentity from "./StockIdentity";
import { useRef } from "react";
import { useMemo, useState } from "react";
import { downloadCsv, useResource } from "../api";
import {
  strategies,
  type ComparisonResults as Results,
  type StrategyDetail,
  type StrategyResult,
} from "../comparison";
import {
  formatMoney,
  formatPercent,
  formatResearchParameters,
} from "../format";
import { eventStyles } from "../eventStyles";
import { ErrorBanner, Loading } from "./common";
import { Button } from "./ui/button";
import { Card } from "./ui/card";
import { ComparisonChart, MetricBars, TradeChart } from "./ComparisonCharts";

const metrics = [
  { key: "return_rate", label: "累计收益率", format: formatPercent },
  {
    key: "annualized_return",
    label: "年化收益（CAGR）",
    format: formatPercent,
  },
  { key: "max_drawdown", label: "最大回撤", format: formatPercent },
  {
    key: "total_assets",
    label: "期末资产",
    format: (value: number | null | undefined) =>
      formatMoney(value, " 元", "未知"),
  },
  {
    key: "profit",
    label: "累计盈亏",
    format: (value: number | null | undefined) =>
      formatMoney(value, " 元", "未知"),
  },
  {
    key: "buy_count",
    label: "买入次数",
    format: (value: number | null | undefined) => String(value ?? "未知"),
  },
  {
    key: "sell_count",
    label: "卖出次数",
    format: (value: number | null | undefined) => String(value ?? "未知"),
  },
  {
    key: "fees",
    label: "交易费用",
    format: (value: number | null | undefined) =>
      formatMoney(value, " 元", "未知"),
  },
  {
    key: "dividend_tax_paid",
    label: "已扣分红税",
    format: (value: number | null | undefined) =>
      formatMoney(value, " 元", "未知"),
  },
  {
    key: "excess_return",
    label: "相对买入持有（百分点）",
    format: (value: number | null | undefined) =>
      value == null ? "无可用基准" : (value * 100).toFixed(2),
  },
] as const;
type MetricKey = (typeof metrics)[number]["key"];
function metricValue(
  item: StrategyResult,
  key: MetricKey,
): number | null | undefined {
  return key === "excess_return" ? item.excess_return : item.summary[key];
}

export default function ComparisonResults({
  data,
  runId,
}: {
  data: Results;
  runId: string;
}) {
  const detailRegion = useRef<HTMLDivElement>(null);
  const [detailHeight, setDetailHeight] = useState(0);
  const stock = (
    <StockIdentity
      code={data.parameters.stock_code}
      date={data.parameters.start_date}
      version={data.data_versions.stock_basic}
    />
  );
  const [hidden, setHidden] = useState<string[]>([]);
  const [sort, setSort] = useState<MetricKey>("return_rate");
  const [ascending, setAscending] = useState(false);
  const [selected, setSelected] = useState(data.strategies[0].strategy_id);
  const [page, setPage] = useState(1);
  const [error, setError] = useState("");
  const detail = useResource<StrategyDetail>(
    `/backtest/runs/${runId}/strategies/${selected}?page=${page}&page_size=50`,
  );
  const visible = useMemo(
    () => data.strategies.filter((item) => !hidden.includes(item.strategy_id)),
    [data.strategies, hidden],
  );
  const sorted = useMemo(
    () =>
      [...data.strategies].sort((a, b) => {
        if (a.summary.complete !== b.summary.complete)
          return a.summary.complete ? -1 : 1;
        const left = metricValue(a, sort),
          right = metricValue(b, sort);
        if (left == null) return right == null ? 0 : 1;
        if (right == null) return -1;
        return (left - right) * (ascending ? 1 : -1);
      }),
    [data.strategies, sort, ascending],
  );
  function chooseSort(key: MetricKey) {
    setAscending(key === sort ? !ascending : key === "max_drawdown");
    setSort(key);
  }
  return (
    <div className="space-y-5">
      <ErrorBanner message={error || detail.error} />
      <p role="status">
        策略完成计算 {data.summary.strategy_count} 个 · 结果完整{" "}
        {data.summary.complete_count} 个
      </p>
      {!data.summary.complete && (
        <p
          role="alert"
          className="rounded-lg bg-amber-50 p-3 text-sm text-amber-800"
        >
          部分策略完整收益未知；曲线只展示可计算部分，不参与最终收益排名。
        </p>
      )}
      <Card className="p-5">
        <h2 className="mb-3">累计收益与回撤 · {stock}</h2>
        <div className="mb-4 flex flex-wrap gap-2">
          {data.strategies.map((item) => (
            <Button
              type="button"
              variant="outline"
              key={item.strategy_id}
              aria-pressed={!hidden.includes(item.strategy_id)}
              className={
                hidden.includes(item.strategy_id)
                  ? "bg-muted text-muted-foreground line-through"
                  : "border-primary/40 bg-emerald-50 text-primary"
              }
              onClick={() =>
                setHidden((current) =>
                  current.includes(item.strategy_id)
                    ? current.filter((id) => id !== item.strategy_id)
                    : [...current, item.strategy_id],
                )
              }
            >
              <span
                className="mr-2 inline-block h-2 w-2 rounded-full"
                style={{
                  backgroundColor: hidden.includes(item.strategy_id)
                    ? "#94a3b8"
                    : strategies.find(
                        (strategy) => strategy.id === item.strategy_id,
                      )!.color,
                }}
              />
              {item.label}
              {!item.summary.complete ? "（部分估算）" : ""}
            </Button>
          ))}
        </div>
        {visible.length ? (
          <>
            <h3 className="text-sm">累计收益率 · {stock}</h3>
            <ComparisonChart results={visible} field="return_rate" />
            <h3 className="mt-4 text-sm">回撤 · {stock}</h3>
            <ComparisonChart results={visible} field="drawdown" />
          </>
        ) : (
          <p
            role="status"
            className="rounded-lg bg-muted p-5 text-sm text-muted-foreground"
          >
            所有策略曲线已隐藏，请点击策略按钮重新显示。
          </p>
        )}
        <p className="mt-3 text-xs text-muted-foreground">
          收益包含费用、已确认现金分红及确定税负；回撤峰值包含初始资金。虚线为部分估算，止于可确认日期。
        </p>
      </Card>
      <Card className="p-5">
        <h2 className="mb-3">指标对比 · {stock}</h2>
        <div className="grid gap-4 md:grid-cols-2">
          <div>
            <h3 className="mb-2 text-sm">累计收益率 · {stock}</h3>
            <MetricBars results={data.strategies} field="return_rate" />
          </div>
          <div>
            <h3 className="mb-2 text-sm">最大回撤 · {stock}</h3>
            <MetricBars results={data.strategies} field="max_drawdown" />
          </div>
        </div>
        <div className="mt-4 max-w-full overflow-x-auto">
          <table className="w-full text-sm" aria-label="策略对比汇总">
            <thead>
              <tr>
                <th>策略 / 完整性</th>
                {metrics.map((metric) => (
                  <th
                    key={metric.key}
                    aria-sort={
                      sort === metric.key
                        ? ascending
                          ? "ascending"
                          : "descending"
                        : "none"
                    }
                  >
                    <button
                      type="button"
                      aria-label={metric.label}
                      className="inline-flex items-center gap-1 rounded py-1 focus-visible:outline-2 focus-visible:outline-primary"
                      onClick={() => chooseSort(metric.key)}
                    >
                      {metric.label}
                      <span
                        aria-hidden="true"
                        className={
                          sort === metric.key
                            ? "text-primary"
                            : "text-muted-foreground/50"
                        }
                      >
                        {sort === metric.key ? (ascending ? "↑" : "↓") : "↕"}
                      </span>
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map((item) => (
                <tr key={item.strategy_id}>
                  <th scope="row" className="whitespace-nowrap">
                    {item.label}
                    <p className="text-xs font-normal">
                      {item.summary.complete ? "完整" : "数据不足"}
                    </p>
                  </th>
                  {metrics.map((metric) => (
                    <td
                      className="whitespace-nowrap tabular-nums"
                      key={metric.key}
                    >
                      {metricValue(item, metric.key) == null &&
                      metric.key !== "excess_return"
                        ? "未知"
                        : metric.format(metricValue(item, metric.key))}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          {[
            ["summary", "指标汇总"],
            ["curves", "每日曲线"],
            ["ledger", "完整账本"],
          ].map(([view, label]) => (
            <Button
              key={view}
              variant="outline"
              onClick={() =>
                void downloadCsv(
                  `/backtest/runs/${runId}/export?view=${view}`,
                  `backtest-${runId}-${view}.csv`,
                ).catch((reason) =>
                  setError(
                    reason instanceof Error ? reason.message : "导出失败",
                  ),
                )
              }
            >
              导出{label} CSV
            </Button>
          ))}
        </div>
      </Card>
      <Card className="p-5">
        <h2 className="mb-3">股价与买卖点 / 策略详情 · {stock}</h2>
        <fieldset>
          <legend className="mb-2 text-sm text-muted-foreground">
            查看策略
          </legend>
          <div className="flex flex-wrap gap-2">
            {data.strategies.map((item) => (
              <label
                key={item.strategy_id}
                className={`!flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2 ${selected === item.strategy_id ? "border-primary/40 bg-emerald-50 text-primary" : "border-border"}`}
              >
                <input
                  type="radio"
                  name={`strategy-${runId}`}
                  value={item.strategy_id}
                  checked={selected === item.strategy_id}
                  onChange={() => {
                    setDetailHeight(
                      detailRegion.current?.getBoundingClientRect().height ?? 0,
                    );
                    setSelected(item.strategy_id);
                    setPage(1);
                  }}
                />
                {item.label}
              </label>
            ))}
          </div>
        </fieldset>
        <div
          ref={detailRegion}
          style={{ minHeight: detailHeight || undefined }}
        >
          {detail.data?.strategy_id === selected ? (
            <>
              <p className="my-3 text-xs text-muted-foreground">
                未复权日K线；箭头表示模拟实际成交，延期信号不作为成交。确认截至{" "}
                {detail.data.summary.confirmed_through ?? "未知"} · 未成交订单{" "}
                {detail.data.summary.pending_orders} 笔。
              </p>
              <TradeChart detail={detail.data} />
              {detail.data.summary.pending_order && (
                <p className="text-xs">
                  待成交：
                  {detail.data.summary.pending_order.side === "buy"
                    ? "买入"
                    : "卖出"}
                  ，信号日 {detail.data.summary.pending_order.signal_date}
                </p>
              )}
              {detail.data.warnings.map((warning, index) => (
                <ErrorBanner key={index} message={warning} />
              ))}
              <details className="my-3">
                <summary>策略参数与模拟假设</summary>
                <pre className="overflow-auto text-xs">
                  {formatResearchParameters(detail.data.parameters)}
                </pre>
                {Object.values(detail.data.assumptions).map((text, index) => (
                  <p key={index} className="mt-2 text-xs">
                    {text}
                  </p>
                ))}
              </details>
              <h3 className="my-3">逐笔账本</h3>
              <div className="overflow-x-auto">
                <table className="w-full text-sm" aria-label="策略逐笔账本">
                  <thead>
                    <tr>
                      {[
                        "实际日期",
                        "信号日期",
                        "事件",
                        "股数变动",
                        "价格",
                        "金额",
                        "费用",
                        "分红税",
                        "现金",
                        "持仓",
                        "说明",
                      ].map((label) => (
                        <th key={label}>{label}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {detail.data.items.map((row, index) => (
                      <tr key={index} className={eventStyles[row.event]?.row}>
                        <td>{row.date}</td>
                        <td>{row.signal_date ?? "—"}</td>
                        <td>
                          {(
                            {
                              contribution: "投入",
                              buy: "买入",
                              sell: "卖出",
                              signal: "策略信号",
                              dividend: "分红到账",
                              entitlement: "分红权益登记",
                              deferred: "延期",
                              insufficient_cash: "余额不足",
                              order_cancelled: "撤销订单",
                              dividend_tax: "分红补税",
                              unknown: "数据不足",
                            } as Record<string, string>
                          )[row.event] ?? row.event}
                        </td>
                        <td>{row.shares_delta}</td>
                        <td>{formatMoney(row.price, "", "—")}</td>
                        <td>{formatMoney(row.amount)}</td>
                        <td>{formatMoney(row.fee)}</td>
                        <td>{formatMoney(row.tax)}</td>
                        <td>{formatMoney(row.cash)}</td>
                        <td>{row.shares}</td>
                        <td>{row.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="mt-4 flex items-center justify-between gap-2 text-sm">
                <span>
                  共 {detail.data.total} 条 · 第 {page} 页
                </span>
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    disabled={page <= 1}
                    onClick={() => setPage(page - 1)}
                  >
                    上一页
                  </Button>
                  <Button
                    variant="outline"
                    disabled={page * 50 >= detail.data!.total}
                    onClick={() => setPage(page + 1)}
                  >
                    下一页
                  </Button>
                </div>
              </div>
            </>
          ) : (
            !detail.error && <Loading />
          )}
        </div>
      </Card>
      <details className="rounded-lg border p-4">
        <summary>本次比较的参数</summary>
        <pre className="mt-3 overflow-auto text-xs">
          {formatResearchParameters(data.parameters)}
        </pre>
      </details>
    </div>
  );
}
