import StockIdentity from "../components/StockIdentity";
import ModuleTabs from "../components/ModuleTabs";
import { eventStyles } from "../eventStyles";
import { dcaPeriodStart } from "../dcaDates";
import { TaskHistory } from "../components/TaskHistory";
import { Fragment, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, downloadCsv, useResource } from "../api";
import {
  activeJob,
  formatResearchParameters,
  statusLabels,
  formatMoney,
  formatPercent,
  performanceClass,
} from "../format";
import { ErrorBanner, Loading, PageTitle } from "../components/common";
import StockInput from "../components/StockInput";
import DcaChart from "../components/DcaChart";
import StrategySelection from "../components/StrategySelection";
import ComparisonResultsView from "../components/ComparisonResults";
import {
  isComparison,
  strategyDefaults,
  type StrategyConfig,
  type ComparisonResults,
  type ComparisonJob,
} from "../comparison";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import type {
  BacktestDefinition,
  BacktestJob,
  BacktestOptions,
  BacktestResults,
} from "../types";

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
  const [historyRevision, setHistoryRevision] = useState(0);
  const [query, setQuery] = useSearchParams();
  const runId = query.get("run");
  const task = useResource<BacktestJob | ComparisonJob>(
    runId ? `/backtest/runs/${runId}` : null,
    2000,
  );
  const [page, setPage] = useState(1);
  const result = useResource<BacktestResults | ComparisonResults>(
    runId && task.data?.id === runId && task.data?.status === "succeeded"
      ? `/backtest/runs/${runId}/results?page=${page}&page_size=50`
      : null,
  );
  const [definition, setDefinition] = useState<BacktestDefinition>(initial);
  const [selected, setSelected] = useState<StrategyConfig[]>(strategyDefaults);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [periodNotice, setPeriodNotice] = useState("");
  const initialized = useRef(false);
  const loaded = useRef("");
  useEffect(() => {
    if (options.data && !initialized.current && !runId) {
      initialized.current = true;
      if (options.data.comparison_defaults)
        setSelected(options.data.comparison_defaults.strategies);
      const end = options.data.latest_market_date ?? "";
      const start = dcaPeriodStart(end, 1, options.data);
      if (end && !start)
        setPeriodNotice(
          "交易日历不足，无法设置一年前的开始交易日，请补齐日历或手动选择。",
        );
      setDefinition((d) => ({
        ...d,
        ...options.data!.defaults,
        start_date: start,
        end_date: end,
        data_versions: options.data!.data_versions,
      }));
    }
  }, [options.data, runId]);
  useEffect(() => {
    if (task.data && task.data.id !== loaded.current) {
      loaded.current = task.data.id;
      initialized.current = true;
      if ("strategies" in task.data.payload) {
        const { strategies, ...common } = task.data.payload;
        setDefinition({ ...initial, ...common });
        setSelected(strategies);
      } else {
        setDefinition(task.data.payload);
        setSelected(
          task.data.payload.strategy === "buy_hold"
            ? [{ id: "buy_hold" }]
            : [
                {
                  id: "ma_cross",
                  short_window: task.data.payload.short_window,
                  long_window: task.data.payload.long_window,
                },
              ],
        );
      }
      setPage(1);
    }
  }, [task.data]);
  useEffect(() => {
    if (task.data?.id === runId && task.data && !activeJob(task.data.status)) {
      setHistoryRevision((value) => value + 1);
    }
  }, [task.data?.id, task.data?.status, runId]);
  const completedNavigation = useRef("");
  useEffect(() => {
    if (
      task.data?.id === runId &&
      task.data?.status === "succeeded" &&
      completedNavigation.current !== runId
    ) {
      completedNavigation.current = runId;
      setQuery(
        (current) => {
          const next = new URLSearchParams(current);
          next.set("tab", "result");
          return next;
        },
        { replace: true },
      );
    }
  }, [task.data?.id, task.data?.status, runId, setQuery]);
  function update<K extends keyof BacktestDefinition>(
    key: K,
    value: BacktestDefinition[K],
  ) {
    setDefinition((d) => ({ ...d, [key]: value }));
  }
  function setPeriod(years: number) {
    if (!options.data) return;
    if (
      definition.data_versions.trade_cal !==
      options.data.data_versions.trade_cal
    ) {
      setError("历史任务的交易日历与当前不同，请手动选择日期。");
      return;
    }
    const start = dcaPeriodStart(definition.end_date, years, options.data);
    if (!start) {
      setError("交易日历不足，无法设置该模拟区间，请补齐日历或手动选择。");
      return;
    }
    setError("");
    setPeriodNotice("");
    update("start_date", start);
  }
  async function execute() {
    setBusy(true);
    setError("");
    try {
      if (!selected.length) throw new Error("请至少选择一个策略");
      const {
        strategy: _strategy,
        short_window: _short,
        long_window: _long,
        ...common
      } = definition;
      const job = await api<ComparisonJob>("/backtest/compare", {
        method: "POST",
        body: JSON.stringify({ ...common, strategies: selected }),
      });
      setPage(1);
      setQuery({ run: job.id });
      setHistoryRevision((value) => value + 1);
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
      setHistoryRevision((value) => value + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : "取消失败");
    }
  }
  const resultReady =
    task.data?.id === runId && task.data?.status === "succeeded";
  const comparison =
    runId && resultReady && result.data && isComparison(result.data)
      ? result.data
      : undefined;
  const data =
    runId && resultReady && result.data && !isComparison(result.data)
      ? result.data
      : undefined;
  const summary = data?.summary;
  return (
    <>
      <PageTitle
        title="单股回测"
        subtitle="六策略对比 · 收益与回撤曲线 · 股价买卖点 · 次日开盘模拟"
        action={
          <Button asChild variant="outline" size="sm">
            <Link to="/knowledge" target="_blank" rel="noopener noreferrer">
              策略说明<span className="sr-only">（新窗口）</span>
            </Link>
          </Button>
        }
      />
      <div className="space-y-5">
        <ErrorBanner
          message={error || options.error || task.error || result.error}
        />
        {(comparison?.fixture_mode ??
          data?.fixture_mode ??
          options.data?.fixture_mode) && (
          <p className="rounded-lg bg-amber-50 p-4 text-sm text-amber-800">
            测试数据模式（fixture）：结果使用人工构造行情。
          </p>
        )}
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

        <ModuleTabs
          mainLabel="回测"
          resultLabel="回测结果"
          result={
            <>
              {task.data?.status === "succeeded" &&
                !data &&
                !comparison &&
                !result.error && <Loading />}
              {comparison && runId && (
                <ComparisonResultsView
                  key={runId}
                  data={comparison}
                  runId={runId}
                />
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
                  <Card className="overflow-hidden">
                    <div className="overflow-x-auto">
                      <table
                        aria-label="回测汇总"
                        className="dca-summary w-full text-sm"
                      >
                        <thead>
                          <tr>
                            <th scope="col">指标</th>
                            <th scope="col">金额 / 数值</th>
                            <th scope="col">指标</th>
                            <th scope="col">金额 / 数值</th>
                          </tr>
                        </thead>
                        <tbody>
                          {[
                            [
                              ["初始资金", money(summary.contributed), null],
                              ["总资产", money(summary.total_assets), null],
                            ],
                            [
                              [
                                "累计盈亏",
                                money(summary.profit),
                                summary.profit,
                              ],
                              [
                                "累计收益率",
                                formatPercent(summary.return_rate, "未知"),
                                summary.return_rate,
                              ],
                            ],
                            [
                              [
                                "年化收益（CAGR）",
                                formatPercent(
                                  summary.annualized_return,
                                  "未知",
                                ),
                                summary.annualized_return,
                              ],
                              [
                                "最大回撤",
                                formatPercent(summary.max_drawdown, "未知"),
                                summary.max_drawdown == null
                                  ? null
                                  : -summary.max_drawdown,
                              ],
                            ],
                            [
                              ["现金余额", money(summary.cash), null],
                              ["持仓股数", `${summary.shares} 股`, null],
                            ],
                            [
                              ["持仓市值", money(summary.position_value), null],
                              [
                                "应收分红",
                                money(summary.receivable),
                                summary.receivable,
                              ],
                            ],
                            [
                              [
                                "已到账分红",
                                money(summary.dividends),
                                summary.dividends,
                              ],
                              ["交易费用", money(summary.fees), null],
                            ],
                            [
                              [
                                "分红补税",
                                money(summary.dividend_tax_paid),
                                null,
                              ],
                              [
                                "待扣分红税",
                                money(summary.tax_liability),
                                null,
                              ],
                            ],
                            [
                              [
                                "买入 / 卖出次数",
                                `${summary.buy_count} / ${summary.sell_count}`,
                                null,
                              ],
                              [
                                "确认截至",
                                summary.confirmed_through ?? "未知",
                                null,
                              ],
                            ],
                          ].map((pair, index) => (
                            <tr key={index}>
                              {pair.map(([label, value, performance]) => (
                                <Fragment key={String(label)}>
                                  <th
                                    scope="row"
                                    className="whitespace-nowrap font-normal text-muted-foreground"
                                  >
                                    {label}
                                  </th>
                                  <td
                                    className={`whitespace-nowrap font-semibold tabular-nums ${performanceClass(typeof performance === "number" ? performance : null)}`}
                                  >
                                    {value}
                                  </td>
                                </Fragment>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </Card>
                  <Card className="p-5">
                    <p className="text-sm">
                      总资产与初始资金 ·{" "}
                      <StockIdentity
                        code={data.parameters.stock_code}
                        date={data.parameters.start_date}
                        version={data.data_versions.stock_basic}
                      />
                      {!summary.complete ? "（可计算部分）" : ""}
                    </p>
                    <p className="my-2 text-xs text-muted-foreground">
                      确认截至 {summary.confirmed_through ?? "未知"} ·
                      持仓估值日 {summary.valuation_date ?? "无持仓估值"} ·
                      未成交订单 {summary.pending_orders} 笔
                    </p>
                    <DcaChart points={data.curve} />
                    {summary.pending_order && (
                      <p className="mt-3 text-xs text-muted-foreground">
                        待成交：
                        {summary.pending_order.side === "buy" ? "买入" : "卖出"}
                        ，信号日 {summary.pending_order.signal_date}。
                      </p>
                    )}
                    <p className="mt-3 text-xs text-muted-foreground">
                      收益率 =（现金 + 持仓市值 + 应收分红 − 确定税负 −
                      初始资金）/ 初始资金。年化收益 =（总资产 / 初始资金）^(365
                      / 回测天数) −
                      1，按开始日到确认截至日的日历天数计算；零时长或数据不完整时不计算。停牌沿用最近可确认收盘价。
                    </p>
                    {Object.entries(data.assumptions).map(([key, value]) => (
                      <p
                        key={key}
                        className="mt-2 text-xs text-muted-foreground"
                      >
                        {value}
                      </p>
                    ))}
                    <details className="mt-3 text-xs">
                      <summary>本次结果使用的参数</summary>
                      <pre className="mt-3 overflow-auto">
                        {formatResearchParameters(data.parameters)}
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
                            setError(
                              e instanceof Error ? e.message : "导出失败",
                            ),
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
                            <tr
                              key={`${row.date}-${index}`}
                              className={eventStyles[row.event]?.row}
                            >
                              <td>{row.date}</td>
                              <td>{row.planned_dates.join(" / ") || "—"}</td>
                              <td>
                                <span
                                  className={`inline-block whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ${eventStyles[row.event]?.badge ?? "bg-slate-100 text-slate-700"}`}
                                >
                                  {events[row.event] ?? row.event}
                                </span>
                              </td>
                              <td>{row.shares_delta}</td>
                              <td>
                                {row.price == null ? "—" : money(row.price)}
                              </td>
                              <td
                                className={performanceClass(
                                  ["dividend", "entitlement"].includes(
                                    row.event,
                                  )
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
              {!runId && <p>请先提交任务或查看历史任务。</p>}
            </>
          }
          history={(active) => (
            <Card className="p-5">
              <h2 className="mb-3">历史回测任务</h2>
              <TaskHistory
                kind="backtest"
                active={active}
                refreshKey={historyRevision}
                onSelect={(id) => {
                  setPage(1);
                  setQuery({ run: id, tab: "result" });
                }}
                onDeleted={(id) => {
                  if (runId === id) {
                    setPage(1);
                    setQuery((params) => {
                      params.delete("run");
                      return params;
                    });
                  }
                }}
              />
            </Card>
          )}
        >
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
              </div>
              <div className="mt-4 flex flex-wrap items-center gap-2">
                <span className="text-xs text-muted-foreground">快捷设置</span>
                {[2, 3, 5].map((years) => (
                  <Button
                    key={years}
                    type="button"
                    variant="outline"
                    disabled={!options.data || !definition.end_date}
                    onClick={() => setPeriod(years)}
                  >
                    模拟{years}年
                  </Button>
                ))}
                <span className="text-xs text-muted-foreground">
                  以结束日向前推算，休市顺延至交易日
                </span>
              </div>
              {periodNotice && (
                <p role="status" className="mt-2 text-xs text-amber-800">
                  {periodNotice}
                </p>
              )}
              <div className="mt-4 max-w-sm">
                <label>
                  初始资金（元）
                  <input
                    aria-label="初始资金（元）"
                    required
                    type="number"
                    min="0.01"
                    max="100000000"
                    step="0.01"
                    value={definition.initial_cash}
                    onChange={(e) => update("initial_cash", e.target.value)}
                  />
                </label>
              </div>
              <StrategySelection selected={selected} onChange={setSelected} />
              <p className="mt-3 text-xs text-muted-foreground">
                收盘信号次日开盘执行。双均线等待区间内新金叉买入、死叉卖出，开始时已有多头不立即建仓。
                一次投入、全仓买卖、期末保留持仓；T+1、停牌及开盘涨跌停限制成交。日线成交仅为模拟假设。
              </p>
              <details className="mt-5 rounded-lg border p-4">
                <summary className="cursor-pointer text-sm font-medium">
                  交易费用
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
                <label className="mt-4 block">
                  卖出印花税率（万分之）
                  <input
                    aria-label="卖出印花税率（万分之）"
                    required
                    type="number"
                    min="0"
                    max="100"
                    step="0.01"
                    value={Number(
                      (Number(definition.stamp_rate) * 10000).toFixed(8),
                    )}
                    onChange={(e) =>
                      update(
                        "stamp_rate",
                        String(Number(e.target.value) / 10000),
                      )
                    }
                  />
                </label>
                <p className="my-3 text-xs text-muted-foreground">
                  佣金包含经手费及证管费，过户费及卖出印花税单列。全区间使用固定参数，不重建历史费率。卖出按持有期计算分红补税，期末持仓未计未来卖出补税。
                </p>
                <p className="text-xs text-muted-foreground">
                  仅分析存续证券，存在幸存者偏差。
                </p>
              </details>
              <Button
                className="mt-5"
                type="submit"
                disabled={
                  busy ||
                  !selected.length ||
                  !options.data ||
                  !Object.keys(definition.data_versions).length ||
                  (!!runId && !task.data)
                }
              >
                {busy ? "正在提交…" : "开始回测"}
              </Button>
            </Card>
          </form>
        </ModuleTabs>
      </div>
    </>
  );
}
