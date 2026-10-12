import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, downloadCsv, useResource } from "../api";
import {
  activeJob,
  formatMoney,
  formatPercent,
  formatResearchParameters,
  performanceClass,
  statusLabels,
} from "../format";
import { dcaPeriodStart } from "../dcaDates";
import {
  strategies,
  parameterLabels,
  warmupDays,
  type StrategyId,
  type StrategyConfig,
} from "../comparison";
import {
  frequencies,
  type PortfolioDefinition,
  type PortfolioJob,
  type PortfolioMember,
  type PortfolioOptions,
  type PortfolioPositions,
  type PortfolioResults,
  type PortfolioStockDetail,
} from "../portfolio";
import { ErrorBanner, Loading, PageTitle } from "../components/common";
import ModuleTabs from "../components/ModuleTabs";
import { TaskHistory } from "../components/TaskHistory";
import StockInput from "../components/StockInput";
import WatchlistPicker from "../components/WatchlistPicker";
import StockIdentity from "../components/StockIdentity";
import PortfolioCharts from "../components/PortfolioCharts";
import { TradeChart } from "../components/ComparisonCharts";
import { Card } from "../components/ui/card";
import { Button } from "../components/ui/button";
import { eventStyles } from "../eventStyles";

const initial: PortfolioDefinition = {
  members: [],
  allocation: "equal",
  rebalance: "monthly",
  initial_cash: "100000",
  start_date: "",
  end_date: "",
  commission_rate: "0.0003",
  minimum_commission: "5",
  transfer_rate: "0.00001",
  stamp_rate: "0.0005",
  data_versions: {},
};
const labels: Record<string, string> = {
  contribution: "初始投入",
  buy: "买入",
  sell: "卖出",
  signal: "策略信号",
  rebalance: "周期调仓",
  order_cancelled: "撤销目标",
  dividend: "分红到账",
  entitlement: "权益登记",
  dividend_tax: "分红补税",
  deferred: "交易延期",
  insufficient_cash: "资金或交易单位不足",
  below_lot: "不足交易单位",
  unknown: "数据未知",
};
const money = (value: number | null | undefined) =>
  formatMoney(value, " 元", "未知");

function defaultWeights(members: PortfolioMember[]): PortfolioMember[] {
  const sorted = [...members].sort((a, b) =>
    a.stock_code.localeCompare(b.stock_code),
  );
  const base = Math.floor(1000000 / Math.max(1, members.length));
  const rest = 1000000 - base * members.length;
  return members.map((member) => ({
    ...member,
    weight: (
      (base +
        (sorted.findIndex((m) => m.stock_code === member.stock_code) < rest
          ? 1
          : 0)) /
      1000000
    ).toFixed(6),
  }));
}

export default function Portfolio() {
  const options = useResource<PortfolioOptions>("/portfolio/options");
  const [query, setQuery] = useSearchParams();
  const run = query.get("run");
  const task = useResource<PortfolioJob>(
    run ? `/portfolio/runs/${run}` : null,
    2000,
  );
  const [definition, setDefinition] = useState<PortfolioDefinition>(initial);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [page, setPage] = useState(1);
  const [account, setAccount] = useState<"portfolio" | "benchmark">(
    "portfolio",
  );
  const [day, setDay] = useState("");
  const [stock, setStock] = useState("");
  const initialized = useRef(false);
  const loaded = useRef("");
  const navigated = useRef("");
  const ready = task.data?.id === run && task.data.status === "succeeded";
  const result = useResource<PortfolioResults>(
    ready ? `/portfolio/runs/${run}/results` : null,
  );
  const ledger = useResource<Pick<PortfolioResults, "items" | "total">>(
    ready
      ? `/portfolio/runs/${run}/results?page=${page}&page_size=50&account=${account}`
      : null,
  );
  const positions = useResource<PortfolioPositions>(
    ready
      ? `/portfolio/runs/${run}/positions?account=${account}${day ? `&date=${day}` : ""}`
      : null,
  );
  const detail = useResource<PortfolioStockDetail>(
    ready && stock
      ? `/portfolio/runs/${run}/stocks/${stock}?account=${account}`
      : null,
  );
  useEffect(() => {
    if (options.data && !initialized.current && !run) {
      initialized.current = true;
      const end = options.data.latest_market_date ?? "";
      const start = dcaPeriodStart(end, 1, options.data);
      setDefinition((d) => ({
        ...d,
        ...options.data!.defaults,
        start_date: start,
        end_date: end,
        data_versions: options.data!.data_versions,
      }));
      if (end && !start)
        setError("交易日历不足，请补齐日历或手动选择开始日期。");
    }
  }, [options.data, run]);
  useEffect(() => {
    if (task.data && task.data.id !== loaded.current) {
      loaded.current = task.data.id;
      initialized.current = true;
      setDefinition(task.data.payload);
      setPage(1);
      setDay("");
      setAccount("portfolio");
      setStock(task.data.payload.members[0]?.stock_code ?? "");
    }
  }, [task.data]);
  useEffect(() => {
    if (task.data?.id === run && !activeJob(task.data.status))
      setRevision((v) => v + 1);
    if (
      task.data?.id === run &&
      task.data.status === "succeeded" &&
      navigated.current !== run
    ) {
      navigated.current = run!;
      setQuery(
        (current) => {
          const next = new URLSearchParams(current);
          next.set("tab", "result");
          return next;
        },
        { replace: true },
      );
    }
  }, [task.data?.id, task.data?.status, run, setQuery]);
  function update<K extends keyof PortfolioDefinition>(
    key: K,
    value: PortfolioDefinition[K],
  ) {
    setDefinition((d) => ({ ...d, [key]: value }));
  }
  function changeMember(code: string, patch: Partial<PortfolioMember>) {
    setDefinition((d) => ({
      ...d,
      members: d.members.map((m) =>
        m.stock_code === code ? { ...m, ...patch } : m,
      ),
    }));
  }
  function selectStocks(value: string) {
    const codes = value.split(/[,，;；\s]+/).filter(Boolean);
    if (codes.length > 20) {
      setError("单个组合最多20只股票");
      return;
    }
    const members = codes.map(
      (code) =>
        definition.members.find((m) => m.stock_code === code) ?? {
          stock_code: code,
          strategy: { id: "buy_hold" } as StrategyConfig,
        },
    );
    update(
      "members",
      definition.allocation === "custom"
        ? members.map((m) => ({ ...m, weight: m.weight ?? "0" }))
        : members,
    );
  }
  function setPeriod(years: number) {
    if (!options.data) return;
    if (
      definition.data_versions.trade_cal !==
      options.data.data_versions.trade_cal
    ) {
      setError("历史任务交易日历与当前不同，请手动选择日期。");
      return;
    }
    const start = dcaPeriodStart(definition.end_date, years, options.data);
    if (!start) {
      setError("交易日历不足，请补齐日历或手动选择日期。");
      return;
    }
    setError("");
    update("start_date", start);
  }
  async function execute() {
    setBusy(true);
    setError("");
    try {
      if (!definition.members.length) throw new Error("请至少选择一只股票");
      if (
        definition.allocation === "custom" &&
        definition.members.reduce(
          (sum, m) => sum + Math.round(Number(m.weight) * 1000000),
          0,
        ) !== 1000000
      )
        throw new Error("自定义权重合计必须为100%");
      const members = definition.members.map((m) =>
        definition.allocation === "equal"
          ? { stock_code: m.stock_code, strategy: m.strategy }
          : m,
      );
      const job = await api<PortfolioJob>("/portfolio/run", {
        method: "POST",
        body: JSON.stringify({ ...definition, members }),
      });
      setQuery({ run: job.id });
      setRevision((v) => v + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : "回测提交失败");
    } finally {
      setBusy(false);
    }
  }
  async function cancel() {
    try {
      await api(`/portfolio/runs/${run}/cancel`, { method: "POST" });
      task.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "取消失败");
    }
  }
  function deleted(id: string) {
    if (id === run) {
      setQuery((current) => {
        const next = new URLSearchParams(current);
        next.delete("run");
        return next;
      });
      loaded.current = "";
      setDay("");
      setStock("");
    }
    setRevision((v) => v + 1);
  }
  const data = ready ? result.data : undefined;
  const parameters = data?.parameters ?? definition;
  const selected = account === "benchmark" ? data?.benchmark : data;
  return (
    <>
      <PageTitle
        title="组合回测"
        subtitle="固定自选 · 逐股策略 · 共享现金 · 定期再平衡 · 买入持有基准"
      />
      <div className="space-y-5 min-w-0">
        <ErrorBanner
          message={
            error ||
            options.error ||
            task.error ||
            result.error ||
            positions.error ||
            detail.error ||
            ledger.error
          }
        />
        {(data?.fixture_mode ?? options.data?.fixture_mode) && (
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
          </Card>
        )}
        <ModuleTabs
          mainLabel="组合回测"
          resultLabel="回测结果"
          history={(active) => (
            <Card className="p-5">
              <h2 className="mb-3">历史组合任务</h2>
              <TaskHistory
                kind="portfolio"
                active={active}
                refreshKey={revision}
                onSelect={(id) => setQuery({ run: id, tab: "result" })}
                onDeleted={deleted}
              />
            </Card>
          )}
          result={
            <>
              {ready && !data && !result.error && <Loading />}
              {!ready && (
                <p className="text-sm text-muted-foreground">
                  提交任务或从历史任务查看结果。
                </p>
              )}
              {data && (
                <>
                  <Card className="p-5 space-y-4">
                    <h2>组合与买入持有基准</h2>
                    <div className="overflow-x-auto">
                      <table aria-label="组合回测汇总" className="w-full">
                        <thead>
                          <tr>
                            <th>指标</th>
                            <th>实际组合</th>
                            <th>买入持有基准</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr>
                            <td>完整性 / 确认截至</td>
                            {[data.summary, data.benchmark.summary].map(
                              (s, i) => (
                                <td key={i}>
                                  {s.complete ? "完整" : "未知"} /{" "}
                                  {s.confirmed_through ?? "无"}
                                </td>
                              ),
                            )}
                          </tr>
                          {(
                            [
                              ["total_assets", "总资产", false],
                              ["profit", "盈亏", false],
                              ["return_rate", "累计收益率", true],
                              ["annualized_return", "年化收益率（CAGR）", true],
                              ["max_drawdown", "最大回撤", true],
                              ["cash", "现金", false],
                              ["position_value", "持仓市值", false],
                              ["receivable", "应收分红", false],
                              ["tax_liability", "确定税负", false],
                              ["fees", "交易费用", false],
                              ["dividends", "现金分红", false],
                              ["dividend_tax_paid", "已付分红税", false],
                            ] as const
                          ).map(([key, label, percent]) => (
                            <tr key={key}>
                              <td>{label}</td>
                              {[data.summary, data.benchmark.summary].map(
                                (s, i) => (
                                  <td
                                    key={i}
                                    className={
                                      [
                                        "profit",
                                        "return_rate",
                                        "annualized_return",
                                      ].includes(key)
                                        ? performanceClass(s[key])
                                        : key === "max_drawdown"
                                          ? performanceClass(
                                              s[key] == null ? null : -s[key]!,
                                            )
                                          : ""
                                    }
                                  >
                                    {percent
                                      ? formatPercent(s[key], "未知")
                                      : money(s[key])}
                                  </td>
                                ),
                              )}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <p className={performanceClass(data.excess_return)}>
                      相对基准收益差：
                      {data.excess_return == null
                        ? "未知"
                        : `${(data.excess_return * 100).toFixed(2)} 个百分点`}
                    </p>
                    {!data.summary.complete && (
                      <ErrorBanner message="实际组合完整收益未知；曲线仅显示可确认部分。" />
                    )}
                    {[
                      ...data.warnings,
                      ...data.benchmark.warnings.map((w) => `基准：${w}`),
                    ].map((w, i) => (
                      <ErrorBanner key={i} message={w} />
                    ))}
                    <details>
                      <summary className="cursor-pointer text-sm">
                        参数与模拟假设
                      </summary>
                      <pre className="mt-3 whitespace-pre-wrap break-all text-xs">
                        {formatResearchParameters(data.parameters)}
                      </pre>
                      {Object.values(data.assumptions).map((a) => (
                        <p
                          key={a}
                          className="mt-2 text-xs text-muted-foreground"
                        >
                          {a}
                        </p>
                      ))}
                    </details>
                    <div className="flex flex-wrap gap-2">
                      {(
                        [
                          ["summary", "指标汇总"],
                          ["curves", "每日曲线"],
                          ["positions", "每日持仓"],
                          ["ledger", "完整账本"],
                        ] as const
                      ).map(([view, label]) => (
                        <Button
                          key={view}
                          variant="outline"
                          onClick={() =>
                            void downloadCsv(
                              `/portfolio/runs/${run}/export?view=${view}`,
                              `portfolio-${run}-${view}.csv`,
                            ).catch((e) => setError(e.message))
                          }
                        >
                          导出{label} CSV
                        </Button>
                      ))}
                    </div>
                  </Card>
                  <Card className="p-5">
                    <h2 className="mb-3">组合累计收益与回撤</h2>
                    <p className="mb-3 text-xs text-muted-foreground">
                      蓝色：实际组合 · 灰色：买入持有基准 · 虚线：部分估算
                    </p>
                    <div className="grid gap-5 xl:grid-cols-2">
                      <PortfolioCharts result={data} field="return_rate" />
                      <PortfolioCharts result={data} field="drawdown" />
                    </div>
                  </Card>
                  <Card className="p-5 space-y-4">
                    <div className="flex flex-wrap items-end gap-3">
                      <label>
                        查看账户
                        <select
                          aria-label="查看账户"
                          value={account}
                          onChange={(e) => {
                            setAccount(e.target.value as typeof account);
                            setPage(1);
                            setDay("");
                          }}
                        >
                          <option value="portfolio">实际组合</option>
                          <option value="benchmark">买入持有基准</option>
                        </select>
                      </label>
                      <label>
                        持仓日期
                        <select
                          aria-label="持仓日期"
                          value={day}
                          onChange={(e) => setDay(e.target.value)}
                        >
                          <option value="">最后确认日</option>
                          {selected?.curve.map((p) => (
                            <option key={p.date} value={p.date}>
                              {p.date}
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                    <h2>每日持仓</h2>
                    <p className="text-sm">
                      {positions.data?.date ?? "无可确认持仓"} · 现金{" "}
                      {money(positions.data?.cash)} · 现金占比{" "}
                      {formatPercent(positions.data?.cash_weight)}
                    </p>
                    <div className="overflow-x-auto">
                      <table className="w-full" aria-label="每日持仓">
                        <thead>
                          <tr>
                            {[
                              "股票",
                              "策略 / 资格",
                              "股数",
                              "收盘价 / 估值日",
                              "市值",
                              "配置权重",
                              "目标权重",
                              "实际权重",
                              "应收 / 税负",
                            ].map((t) => (
                              <th key={t}>{t}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {positions.data?.items.map((p) => (
                            <tr key={p.stock_code}>
                              <td>
                                <button
                                  className="text-primary underline"
                                  onClick={() => setStock(p.stock_code)}
                                >
                                  <StockIdentity
                                    code={p.stock_code}
                                    date={parameters.start_date}
                                    version={
                                      parameters.data_versions.stock_basic
                                    }
                                  />
                                </button>
                              </td>
                              <td>
                                {
                                  strategies.find((s) => s.id === p.strategy.id)
                                    ?.label
                                }{" "}
                                / {p.eligible ? "启用" : "关闭"}
                              </td>
                              <td>{p.shares}</td>
                              <td>
                                {money(p.price)} / {p.valuation_date ?? "—"}
                              </td>
                              <td>{money(p.position_value)}</td>
                              <td>{formatPercent(p.configured_weight)}</td>
                              <td>{formatPercent(p.target_weight)}</td>
                              <td>{formatPercent(p.actual_weight)}</td>
                              <td>
                                {money(p.receivable)} / {money(p.tax_liability)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <label>
                      查看股票
                      <select
                        aria-label="查看股票"
                        value={stock}
                        onChange={(e) => setStock(e.target.value)}
                      >
                        {parameters.members.map((m) => (
                          <option key={m.stock_code} value={m.stock_code}>
                            {m.stock_code}
                          </option>
                        ))}
                      </select>
                    </label>
                    {detail.data && (
                      <>
                        <h2>
                          <StockIdentity
                            code={stock}
                            date={parameters.start_date}
                            version={parameters.data_versions.stock_basic}
                          />{" "}
                          · 未复权股价与模拟成交
                        </h2>
                        <TradeChart detail={detail.data} />
                      </>
                    )}
                  </Card>
                  <Card className="p-5 space-y-3">
                    <h2>
                      组合交易账本 ·{" "}
                      {account === "portfolio" ? "实际组合" : "基准"}
                    </h2>
                    <div className="overflow-x-auto">
                      <table className="w-full" aria-label="组合交易账本">
                        <thead>
                          <tr>
                            {[
                              "日期",
                              "股票",
                              "事件",
                              "信号日",
                              "股数变动",
                              "价格",
                              "成交额 / 权益",
                              "费用",
                              "税款",
                              "现金",
                              "原因",
                            ].map((t) => (
                              <th key={t}>{t}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {(ledger.data?.items ?? []).map((r, i) => (
                            <tr key={i} className={eventStyles[r.event]?.row}>
                              <td>{r.date}</td>
                              <td>{r.stock_code ?? "组合"}</td>
                              <td>
                                <span
                                  className={`rounded px-2 py-1 text-xs ${eventStyles[r.event]?.badge ?? ""}`}
                                >
                                  {labels[r.event] ?? r.event}
                                </span>
                              </td>
                              <td>{r.signal_date ?? "—"}</td>
                              <td>{r.shares_delta}</td>
                              <td>{r.price == null ? "—" : money(r.price)}</td>
                              <td>{money(r.amount)}</td>
                              <td>{money(r.fee)}</td>
                              <td>{money(r.tax)}</td>
                              <td>{money(r.cash)}</td>
                              <td className="min-w-48 whitespace-normal">
                                {r.reason}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <div className="flex items-center gap-3">
                      <Button
                        variant="outline"
                        disabled={page <= 1}
                        onClick={() => setPage((p) => p - 1)}
                      >
                        上一页
                      </Button>
                      <span>
                        第 {page} /{" "}
                        {Math.max(1, Math.ceil((ledger.data?.total ?? 0) / 50))}{" "}
                        页 · {ledger.data?.total ?? 0} 条
                      </span>
                      <Button
                        variant="outline"
                        disabled={page * 50 >= (ledger.data?.total ?? 0)}
                        onClick={() => setPage((p) => p + 1)}
                      >
                        下一页
                      </Button>
                    </div>
                  </Card>
                </>
              )}
            </>
          }
        >
          <Card className="p-5">
            <form
              className="space-y-5"
              onSubmit={(e) => {
                e.preventDefault();
                void execute();
              }}
            >
              <p className="text-sm text-muted-foreground">
                固定版本存续沪深A股，历史结果存在幸存者偏差。每股一个策略，共享现金；未启用份额留现金。
              </p>
              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
                <label>
                  开始交易日
                  <input
                    type="date"
                    aria-label="开始交易日"
                    required
                    value={definition.start_date}
                    onChange={(e) => update("start_date", e.target.value)}
                  />
                </label>
                <label>
                  结束交易日
                  <input
                    type="date"
                    aria-label="结束交易日"
                    required
                    value={definition.end_date}
                    onChange={(e) => update("end_date", e.target.value)}
                  />
                </label>
                <label>
                  初始资金（元）
                  <input
                    type="number"
                    aria-label="初始资金"
                    min="0.01"
                    max="100000000"
                    step="0.01"
                    required
                    value={definition.initial_cash}
                    onChange={(e) => update("initial_cash", e.target.value)}
                  />
                </label>
                <label>
                  再平衡周期
                  <select
                    aria-label="再平衡周期"
                    value={definition.rebalance}
                    onChange={(e) =>
                      update(
                        "rebalance",
                        e.target.value as PortfolioDefinition["rebalance"],
                      )
                    }
                  >
                    {Object.entries(frequencies).map(([key, label]) => (
                      <option key={key} value={key}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <div className="flex flex-wrap gap-2">
                {[1, 2, 3, 5].map((y) => (
                  <Button
                    type="button"
                    variant="outline"
                    key={y}
                    onClick={() => setPeriod(y)}
                  >
                    模拟{y}年
                  </Button>
                ))}
              </div>
              <StockInput
                label="组合股票"
                value={definition.members.map((m) => m.stock_code).join(", ")}
                onChange={selectStocks}
                multiple
                asOfDate={definition.start_date}
                dataVersion={definition.data_versions.stock_basic}
              />
              <WatchlistPicker
                maximum={20}
                replace
                hasMembers={definition.members.length > 0}
                onApply={(codes) => {
                  setError("");
                  setDefinition((current) => ({
                    ...current,
                    allocation: "equal",
                    members: codes.map((stock_code) => ({
                      stock_code,
                      strategy: { id: "buy_hold" },
                    })),
                  }));
                }}
              />
              <div className="flex flex-wrap items-end gap-3">
                <label>
                  资金分配
                  <select
                    aria-label="资金分配"
                    value={definition.allocation}
                    onChange={(e) =>
                      setDefinition((d) => ({
                        ...d,
                        allocation: e.target
                          .value as PortfolioDefinition["allocation"],
                        members:
                          e.target.value === "custom"
                            ? d.members.some((m) => m.weight != null)
                              ? d.members.map((m) => ({
                                  ...m,
                                  weight: m.weight ?? "0",
                                }))
                              : defaultWeights(d.members)
                            : d.members,
                      }))
                    }
                  >
                    <option value="equal">等权</option>
                    <option value="custom">自定义权重</option>
                  </select>
                </label>
                <p className="text-sm">
                  已选 {definition.members.length} / 20 只
                  {definition.allocation === "custom"
                    ? ` · 权重合计 ${(definition.members.reduce((sum, m) => sum + Math.round(Number(m.weight) * 1000000), 0) / 10000).toFixed(4)}%`
                    : ""}
                </p>
              </div>
              <div className="grid gap-3 lg:grid-cols-2">
                {definition.members.map((member) => {
                  const strategy = strategies.find(
                    (s) => s.id === member.strategy.id,
                  )!;
                  return (
                    <fieldset
                      key={member.stock_code}
                      className="min-w-0 rounded-lg border p-4 space-y-3"
                    >
                      <legend className="px-2">
                        <StockIdentity
                          code={member.stock_code}
                          date={definition.start_date}
                          version={definition.data_versions.stock_basic}
                        />
                      </legend>
                      <div className="grid gap-3 sm:grid-cols-2">
                        <label>
                          策略
                          <select
                            aria-label={`${member.stock_code} 策略`}
                            value={member.strategy.id}
                            onChange={(e) => {
                              const s = strategies.find(
                                (s) => s.id === e.target.value,
                              )!;
                              changeMember(member.stock_code, {
                                strategy: {
                                  id: s.id as StrategyId,
                                  ...s.defaults,
                                },
                              });
                            }}
                          >
                            {strategies.map((s) => (
                              <option key={s.id} value={s.id}>
                                {s.label}
                              </option>
                            ))}
                          </select>
                        </label>
                        {definition.allocation === "custom" && (
                          <label>
                            权重（%）
                            <input
                              aria-label={`${member.stock_code} 权重百分比`}
                              type="number"
                              min="0.0001"
                              max="100"
                              step="0.0001"
                              required
                              value={Number(member.weight ?? 0) * 100}
                              onChange={(e) =>
                                changeMember(member.stock_code, {
                                  weight: (
                                    Number(e.target.value) / 100
                                  ).toFixed(6),
                                })
                              }
                            />
                          </label>
                        )}
                      </div>
                      <p className="text-xs text-muted-foreground">
                        {strategy.rule} · 预热 {warmupDays(member.strategy)}{" "}
                        个交易日
                      </p>
                      {Object.keys(strategy.defaults).length > 0 && (
                        <div className="grid grid-cols-2 gap-3">
                          {Object.keys(strategy.defaults).map((key) => (
                            <label key={key}>
                              {parameterLabels[key]}
                              <input
                                aria-label={`${member.stock_code} ${parameterLabels[key]}`}
                                type="number"
                                required
                                step={
                                  ["lower", "upper", "deviations"].includes(key)
                                    ? "0.1"
                                    : "1"
                                }
                                value={member.strategy[key]}
                                onChange={(e) =>
                                  changeMember(member.stock_code, {
                                    strategy: {
                                      ...member.strategy,
                                      [key]: Number(e.target.value),
                                    },
                                  })
                                }
                              />
                            </label>
                          ))}
                        </div>
                      )}
                    </fieldset>
                  );
                })}
              </div>
              <details>
                <summary className="cursor-pointer text-sm">费用设置</summary>
                <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  {(
                    [
                      ["commission_rate", "佣金率"],
                      ["minimum_commission", "最低佣金（元）"],
                      ["transfer_rate", "过户费率"],
                      ["stamp_rate", "卖出印花税率"],
                    ] as const
                  ).map(([key, label]) => (
                    <label key={key}>
                      {label}
                      <input
                        aria-label={label}
                        type="number"
                        min="0"
                        max={key === "minimum_commission" ? "10000" : "0.01"}
                        step={key === "minimum_commission" ? "0.01" : "0.00001"}
                        value={definition[key]}
                        onChange={(e) => update(key, e.target.value)}
                      />
                    </label>
                  ))}
                </div>
                <p className="mt-2 text-xs text-muted-foreground">
                  全区间固定费率模拟，不重建历史费率。
                </p>
              </details>
              <p className="text-xs text-muted-foreground">
                周期末收盘发单，次日开盘先卖后买；资金不足按缺口比例分配，未买足部分等下次调仓。买入持有基准不再平衡，分红不自动再投入。
              </p>
              <Button
                type="submit"
                disabled={busy || activeJob(task.data?.status ?? "")}
              >
                {busy ? "正在提交…" : "开始组合回测"}
              </Button>
            </form>
          </Card>
        </ModuleTabs>
      </div>
    </>
  );
}
