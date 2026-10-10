import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, downloadCsv, useResource } from "../api";
import {
  activeJob,
  statusLabels,
  formatMoney,
  formatPercent,
  performanceClass,
} from "../format";
import { ErrorBanner, Loading, PageTitle } from "../components/common";
import StockInput from "../components/StockInput";
import DcaChart from "../components/DcaChart";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import type { BacktestDefinition, BacktestJob, BacktestOptions, BacktestResults } from "../types";

const initial: BacktestDefinition = {
  stock_code: "",
  start_date: "",
  end_date: "",
  initial_cash: "100000",
  strategy: "buy_hold",
  short_window: 20,
  long_window: 60,
  stamp_rate: "0.0005",
  commission_rate: "0.0003",
  minimum_commission: "5",
  transfer_rate: "0.00001",
  data_versions: {},
};
const events: Record<string, string> = {
  contribution: "投入",
  buy: "买入",
  dividend: "分红到账",
  entitlement: "分红权益登记",
  deferred: "延期",
  insufficient_cash: "余额不足",
  unknown: "数据不足",
  sell: "卖出",
  signal: "策略信号",
  order_cancelled: "撤销订单",
  dividend_tax: "分红补税",
};
const money = (value: number | null | undefined) =>
  formatMoney(value, " 元", "未知");
export default function Backtest() {
  const options = useResource<BacktestOptions>("/backtest/options");
  const history = useResource<BacktestJob[]>("/jobs?kind=backtest", 5000);
  const [query, setQuery] = useSearchParams();
  const runId = query.get("run");
  const task = useResource<BacktestJob>(runId ? `/backtest/runs/${runId}` : null, 2000);
  const [page, setPage] = useState(1);
  const result = useResource<BacktestResults>(
    task.data?.status === "succeeded"
      ? `/backtest/runs/${runId}/results?page=${page}&page_size=50`
      : null,
  );
  const [definition, setDefinition] = useState<BacktestDefinition>(initial);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const initialized = useRef(false);
  const loaded = useRef("");
  useEffect(() => {
    if (options.data && !initialized.current && !runId) {
      initialized.current = true;
      const end = options.data.latest_market_date ?? "";
      // User explicitly chooses the start trading day; no fabricated calendar defaults.
      setDefinition((d) => ({
        ...d,
        ...options.data!.defaults,
        end_date: end,
        data_versions: options.data!.data_versions,
      }));
    }
  }, [options.data, runId]);
  useEffect(() => {
    if (task.data && task.data.id !== loaded.current) {
      loaded.current = task.data.id;
      initialized.current = true;
      setDefinition(task.data.payload);
      setPage(1);
    }
  }, [task.data]);
  function update<K extends keyof BacktestDefinition>(
    key: K,
    value: BacktestDefinition[K],
  ) {
    setDefinition((d) => ({ ...d, [key]: value }));
  }
  async function execute() {
    setBusy(true);
    setError("");
    try {
      const job = await api<BacktestJob>("/backtest/run", {
        method: "POST",
        body: JSON.stringify(definition),
      });
      setPage(1);
      setQuery({ run: job.id });
      history.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "回测提交失败");
    } finally {
      setBusy(false);
    }
  }
  async function cancel() {
    setError("");
    try {
      await api(`/backtest/runs/${runId}/cancel`, { method: "POST" });
      task.reload();
      history.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "取消失败");
    }
  }
  const data = result.data;
  const summary = data?.summary;
  return (
    <>
      <PageTitle
        title="单股回测"
        subtitle="买入持有 / 双均线交叉 · 次日开盘模拟 · 可核对买卖与税款"
      />
      <div className="space-y-5">
        <ErrorBanner
          message={
            error ||
            options.error ||
            task.error ||
            result.error ||
            history.error
          }
        />
        {(data?.fixture_mode ?? options.data?.fixture_mode) && (
          <p className="rounded-lg bg-amber-50 p-4 text-sm text-amber-800">
            测试数据模式（fixture）：结果使用人工构造行情。
          </p>
        )}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void execute();
          }}
        >
          <Card className="p-5">
            <div className="grid gap-4 md:grid-cols-3">
              <label>
                股票代码
                <StockInput
                  label="股票代码"
                  required
                  value={definition.stock_code}
                  onChange={(value) => update("stock_code", value)}
                  asOfDate={definition.start_date || undefined}
                  dataVersion={definition.data_versions.stock_basic}
                />
              </label>
              <label>
                开始交易日
                <input
                  aria-label="开始交易日"
                  required
                  type="date"
                  value={definition.start_date}
                  onChange={(e) => update("start_date", e.target.value)}
                />
              </label>
              <label>
                结束交易日
                <input
                  aria-label="结束交易日"
                  required
                  type="date"
                  value={definition.end_date}
                  onChange={(e) => update("end_date", e.target.value)}
                />
              </label>
              <label>初始资金（元）
                <input aria-label="初始资金（元）" required type="number" min="0.01" max="100000000" step="0.01"
                  value={definition.initial_cash} onChange={(e) => update("initial_cash", e.target.value)} />
              </label>
              <label>策略
                <select aria-label="策略" value={definition.strategy}
                  onChange={(e) => update("strategy", e.target.value as BacktestDefinition["strategy"])}>
                  <option value="buy_hold">买入持有</option><option value="ma_cross">双均线交叉</option>
                </select>
              </label>
              {definition.strategy === "ma_cross" && <>
                <label>短均线周期<input aria-label="短均线周期" required type="number" min="2" max="249"
                  value={definition.short_window} onChange={(e) => update("short_window", Number(e.target.value))} /></label>
                <label>长均线周期<input aria-label="长均线周期" required type="number" min={definition.short_window + 1} max="250"
                  value={definition.long_window} onChange={(e) => update("long_window", Number(e.target.value))} /></label>
              </>}
            </div>
            <p className="mt-3 text-xs text-muted-foreground">
              收盘信号次日开盘执行。双均线等待区间内新金叉买入、死叉卖出，开始时已有多头不立即建仓。
              一次投入、全仓买卖、期末保留持仓；T+1、停牌及开盘涨跌停限制成交。日线成交仅为模拟假设。
            </p>
            <details className="mt-5 rounded-lg border p-4">
              <summary className="cursor-pointer text-sm font-medium">
                交易费用与数据版本
              </summary>
              <div className="mt-4 grid gap-4 md:grid-cols-3">
                <label>
                  佣金率（万分之）
                  <input
                    aria-label="佣金率（万分之）"
                    required
                    type="number"
                    min="0"
                    max="100"
                    step="0.01"
                    value={Number(
                      (Number(definition.commission_rate) * 10000).toFixed(8),
                    )}
                    onChange={(e) =>
                      update(
                        "commission_rate",
                        String(Number(e.target.value) / 10000),
                      )
                    }
                  />
                </label>
                <label>
                  最低佣金（元）
                  <input
                    aria-label="最低佣金（元）"
                    required
                    type="number"
                    min="0"
                    max="10000"
                    step="0.01"
                    value={definition.minimum_commission}
                    onChange={(e) =>
                      update("minimum_commission", e.target.value)
                    }
                  />
                </label>
                <label>
                  过户费率（万分之）
                  <input
                    aria-label="过户费率（万分之）"
                    required
                    type="number"
                    min="0"
                    max="100"
                    step="0.01"
                    value={Number(
                      (Number(definition.transfer_rate) * 10000).toFixed(8),
                    )}
                    onChange={(e) =>
                      update(
                        "transfer_rate",
                        String(Number(e.target.value) / 10000),
                      )
                    }
                  />
                </label>
              </div>
              <label className="mt-4 block">卖出印花税率（万分之）
                <input aria-label="卖出印花税率（万分之）" required type="number" min="0" max="100" step="0.01"
                  value={Number((Number(definition.stamp_rate) * 10000).toFixed(8))}
                  onChange={(e) => update("stamp_rate", String(Number(e.target.value) / 10000))} />
              </label>
              <p className="my-3 text-xs text-muted-foreground">
                佣金包含经手费及证管费，过户费及卖出印花税单列。全区间使用固定参数，不重建历史费率。卖出按持有期计算分红补税，期末持仓未计未来卖出补税。
              </p>
              <p className="mb-3 text-xs text-muted-foreground">
                初始使用当前已发布版本；可填入已有版本
                ID。历史任务自动恢复原版本，提交后固定。仅分析该版本存续证券，存在幸存者偏差。
              </p>
              <div className="grid gap-3 md:grid-cols-2">
                {Object.entries(definition.data_versions).map(
                  ([name, version]) => (
                    <label key={name} className="text-xs">
                      {name}
                      <input
                        aria-label={`${name} 数据版本`}
                        className="font-mono text-xs"
                        required
                        value={version}
                        onChange={(e) =>
                          update("data_versions", {
                            ...definition.data_versions,
                            [name]: e.target.value,
                          })
                        }
                      />
                    </label>
                  ),
                )}
              </div>
              <Button
                className="mt-3"
                variant="outline"
                type="button"
                disabled={!options.data}
                onClick={() =>
                  update("data_versions", options.data!.data_versions)
                }
              >
                使用当前已发布版本
              </Button>
            </details>
            <Button
              className="mt-5"
              type="submit"
              disabled={
                busy ||
                !options.data ||
                !Object.keys(definition.data_versions).length ||
                (!!runId && !task.data)
              }
            >
              {busy ? "正在提交…" : "开始回测"}
            </Button>
          </Card>
        </form>
        {task.data && (
          <Card className="p-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p>
                任务 {task.data.id.slice(0, 8)} ·{" "}
                {statusLabels[task.data.status]} ·{" "}
                {Math.round(task.data.progress * 100)}%
              </p>
              {activeJob(task.data.status) && (
                <Button variant="outline" onClick={() => void cancel()}>
                  取消任务
                </Button>
              )}
            </div>
            <ErrorBanner message={task.data.error ?? undefined} />
            {task.data.logs?.length ? (
              <details className="mt-3 text-xs">
                <summary>任务日志</summary>
                {task.data.logs.map((log, index) => (
                  <p key={index} className="mt-2">
                    {log.at} · {log.message}
                  </p>
                ))}
              </details>
            ) : null}
          </Card>
        )}
        {task.data?.status === "succeeded" && !data && !result.error && (
          <Loading />
        )}
        {data && summary && (
          <>
            {!summary.complete && (
              <p
                role="alert"
                className="rounded-lg bg-amber-50 p-4 text-sm text-amber-800"
              >
                完整收益未知；账本与曲线仅展示可计算部分，不代表完整结果。
              </p>
            )}
            {data.warnings.map((warning, i) => (
              <ErrorBanner key={i} message={warning} />
            ))}
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {[
                ["初始资金", money(summary.contributed)],
                ["总资产", money(summary.total_assets)],
                ["累计盈亏", money(summary.profit)],
                ["累计收益率", formatPercent(summary.return_rate, "未知")],
                ["现金余额", money(summary.cash)],
                ["持仓股数", `${summary.shares} 股`],
                ["持仓市值", money(summary.position_value)],
                ["应收分红", money(summary.receivable)],
                ["已到账分红", money(summary.dividends)],
                ["交易费用", money(summary.fees)],
                ["分红补税", money(summary.dividend_tax_paid)],
                ["待扣分红税", money(summary.tax_liability)],
                ["最大回撤", formatPercent(summary.max_drawdown, "未知")],
                ["买入 / 卖出次数", `${summary.buy_count} / ${summary.sell_count}`],
              ].map(([label, value]) => (
                <Card key={label} className="p-4">
                  <p className="text-xs text-muted-foreground">{label}</p>
                  <p
                    className={`mt-2 text-lg font-semibold ${performanceClass(
                      label === "累计盈亏"
                        ? summary.profit
                        : label === "最大回撤"
                          ? summary.max_drawdown == null ? null : -summary.max_drawdown
                        : label === "累计收益率"
                          ? summary.return_rate
                          : label === "已到账分红"
                            ? summary.dividends
                            : label === "应收分红"
                              ? summary.receivable
                              : null,
                    )}`}
                  >
                    {value}
                  </p>
                </Card>
              ))}
            </div>
            <Card className="p-5">
              <p className="text-sm">
                总资产与初始资金{!summary.complete ? "（可计算部分）" : ""}
              </p>
              <p className="my-2 text-xs text-muted-foreground">
                确认截至 {summary.confirmed_through ?? "未知"} · 持仓估值日{" "}
                {summary.valuation_date ?? "无持仓估值"} · 未成交订单{" "}
                {summary.pending_orders} 笔
              </p>
              <DcaChart points={data.curve} />
              {summary.pending_order && <p className="mt-3 text-xs text-muted-foreground">
                待成交：{summary.pending_order.side === "buy" ? "买入" : "卖出"}，信号日 {summary.pending_order.signal_date}。
              </p>}
              <p className="mt-3 text-xs text-muted-foreground">
                收益率 =（现金 + 持仓市值 + 应收分红 − 确定税负 − 初始资金）/
                初始资金。未提供年化收益；停牌沿用最近可确认收盘价。
              </p>
              {Object.entries(data.assumptions).map(([key, value]) => (
                <p key={key} className="mt-2 text-xs text-muted-foreground">
                  {value}
                </p>
              ))}
              <details className="mt-3 text-xs">
                <summary>本次结果使用的数据版本及参数</summary>
                <pre className="mt-3 overflow-auto">
                  {JSON.stringify(
                    {
                      data_versions: data.data_versions,
                      parameters: data.parameters,
                    },
                    null,
                    2,
                  )}
                </pre>
              </details>
            </Card>
            <Card className="overflow-hidden p-5">
              <div className="mb-3 flex justify-between">
                <h2>逐笔账本</h2>
                <Button
                  variant="outline"
                  onClick={() => {
                    void downloadCsv(
                      `/backtest/runs/${runId}/export`,
                      `backtest-${runId}.csv`,
                    ).catch((e) =>
                      setError(e instanceof Error ? e.message : "导出失败"),
                    );
                  }}
                >
                  导出完整 CSV
                </Button>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full">
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
                        "现金余额",
                        "持仓",
                        "说明",
                      ].map((label) => (
                        <th key={label}>{label}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((row, index) => (
                      <tr key={`${row.date}-${index}`}>
                        <td>{row.date}</td>
                        <td>{row.planned_dates.join(" / ") || "—"}</td>
                        <td>{events[row.event] ?? row.event}</td>
                        <td>{row.shares_delta}</td>
                        <td>{row.price == null ? "—" : money(row.price)}</td>
                        <td
                          className={performanceClass(
                            ["dividend", "entitlement"].includes(row.event)
                              ? row.amount
                              : null,
                          )}
                        >
                          {money(row.amount)}
                        </td>
                        <td>{money(row.fee)}</td>
                        <td>{money(row.tax)}</td>
                        <td>{money(row.cash)}</td>
                        <td>{row.shares}</td>
                        <td>{row.reason || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="mt-4 flex items-center justify-between text-sm">
                <span>
                  共 {data.total} 条 · 第 {page} 页
                </span>
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    disabled={page <= 1}
                    onClick={() => setPage((p) => p - 1)}
                  >
                    上一页
                  </Button>
                  <Button
                    variant="outline"
                    disabled={page * 50 >= data.total}
                    onClick={() => setPage((p) => p + 1)}
                  >
                    下一页
                  </Button>
                </div>
              </div>
            </Card>
          </>
        )}
        <Card className="p-5">
          <h2 className="mb-3">历史回测任务</h2>
          <div className="space-y-2">
            {history.data?.map((job) => (
              <button
                key={job.id}
                className="block w-full rounded-lg border p-3 text-left text-sm hover:bg-muted"
                onClick={() => {
                  setPage(1);
                  setQuery({ run: job.id });
                }}
              >
                {job.payload.stock_code} · {job.payload.start_date} 至{" "}
                {job.payload.end_date} · {statusLabels[job.status]} ·{" "}
                {job.id.slice(0, 8)}
              </button>
            ))}
            {!history.data?.length && (
              <p className="text-sm text-muted-foreground">暂无任务</p>
            )}
          </div>
        </Card>
      </div>
    </>
  );
}
