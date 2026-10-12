import ModuleTabs from "../components/ModuleTabs";
import { TaskHistory } from "../components/TaskHistory";
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Filter, Save } from "lucide-react";
import { api, useResource } from "../api";
import { activeJob, statusLabels } from "../format";
import { ConditionEditor } from "../components/ConditionEditor";
import StrategySelection from "../components/StrategySelection";
import { type StrategyConfig } from "../comparison";
import ScreeningDescription from "../components/ScreeningDescription";
import StockInput from "../components/StockInput";
import WatchlistPicker from "../components/WatchlistPicker";
import ResultsTable from "../components/ResultsTable";
import { ErrorBanner, Loading, PageTitle } from "../components/common";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import type {
  Factor,
  Group,
  ScreeningJob,
  MarketStatus,
  Parameters,
  Preset,
  Rule,
  ScreeningDefinition,
} from "../types";

const parameterLabels: Record<string, [string, string]> = {
  dividend_min: ["最低股息率", "%"],
  daily_return_max: ["当日涨幅上限", "%"],
  drawdown_min: ["最低60日回撤", "%"],
  amount_min: ["最低20日平均成交额", "元"],
  return_20d_min: ["最低20日收益", "%"],
  return_60d_min: ["最低60日收益", "%"],
  price_ma_min: ["收盘价/MA20下限", "倍"],
  volume_ratio_max: ["5日量比上限", "倍"],
  distance_max: ["MA20绝对偏离上限", "%"],
  ma_growth_min: ["MA20五日增长下限", "%"],
  volume_ratio_min: ["20日量比下限", "倍"],
  breakout_ratio_min: ["收盘价/前20日最高下限", "倍"],
};
export default function Screening() {
  const rules = useResource<{ rules: Rule[]; factors: Factor[] }>(
    "/screening/rules",
  );
  const market = useResource<MarketStatus>("/market/status");
  const presets = useResource<Preset[]>("/screening/presets");
  const [historyRevision, setHistoryRevision] = useState(0);
  const [query, setQuery] = useSearchParams();
  const runId = query.get("run");
  const task = useResource<ScreeningJob>(
    runId ? `/screening/runs/${runId}` : null,
    2000,
  );
  const [ruleId, setRuleId] = useState("A");
  const [mode, setMode] = useState<
    "rule" | "signal" | "rule_signal" | "dividend"
  >("rule");
  const [signalParameters, setSignalParameters] = useState<Parameters>({
    exclude_st: false,
    exclude_suspended: true,
  });
  const [strategies, setStrategies] = useState<StrategyConfig[]>([
    { id: "ma_cross", short_window: 20, long_window: 60 },
  ]);
  const [recentEnabled, setRecentEnabled] = useState(false);
  const [recentDays, setRecentDays] = useState(30);
  const recentInput = useRef<HTMLInputElement>(null);
  const [direction, setDirection] = useState<"buy" | "sell" | "either">("buy");
  const [dividendDraft, setDividendDraft] = useState<string | null>(null);
  const dividendInput = useRef<HTMLInputElement>(null);
  const [parameters, setParameters] = useState<Parameters>({});
  const [day, setDay] = useState("");
  const [universe, setUniverse] = useState<"all" | "custom">("all");
  const [fromWatchlist, setFromWatchlist] = useState(false);
  const [codes, setCodes] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [presetName, setPresetName] = useState("");
  const loadedRun = useRef("");
  useEffect(() => {
    if (rules.data && !Object.keys(parameters).length)
      setParameters(rules.data.rules[0].defaults);
  }, [rules.data, parameters]);
  useEffect(() => {
    if (market.data && !day) setDay(market.data.latest_market_date ?? "");
  }, [market.data, day]);
  function apply(definition: ScreeningDefinition) {
    dividendInput.current?.setCustomValidity("");
    setDividendDraft(null);
    const nextMode = definition.mode ?? "rule";
    setMode(nextMode);
    if (definition.rule_id) setRuleId(definition.rule_id);
    setRecentEnabled(Boolean(definition.dividend_filter));
    setRecentDays(definition.dividend_filter?.days ?? 30);
    if (nextMode === "signal" || nextMode === "dividend")
      setSignalParameters(definition.parameters);
    else setParameters(definition.parameters);
    setStrategies(
      definition.signal_filter?.strategies ?? [
        { id: "ma_cross", short_window: 20, long_window: 60 },
      ],
    );
    setDirection(definition.signal_filter?.direction ?? "buy");
    setDay(definition.as_of_date);
    setUniverse(definition.universe.type);
    setFromWatchlist(false);
    setCodes(definition.universe.codes.join(", "));
  }
  useEffect(() => {
    if (task.data && task.data.id !== loadedRun.current) {
      loadedRun.current = task.data.id;
      apply(task.data.payload);
    }
  }, [task.data]);
  // An explicit historical tab must survive the first completed-job response.
  const completedNavigation = useRef(query.has("tab") ? (runId ?? "") : "");
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
  function definition(): ScreeningDefinition {
    return {
      as_of_date: day,
      mode,
      rule_id: mode === "signal" || mode === "dividend" ? null : ruleId,
      universe: {
        type: universe,
        codes:
          universe === "custom"
            ? codes
                .split(/[,，\s]+/)
                .filter(Boolean)
                .map((c) => c.toUpperCase())
            : [],
      },
      parameters:
        mode === "signal" || mode === "dividend"
          ? signalParameters
          : parameters,
      signal_filter:
        mode === "rule" || mode === "dividend"
          ? null
          : { strategies, direction },
      dividend_filter:
        mode === "dividend" || recentEnabled ? { days: recentDays } : null,
    };
  }
  async function execute() {
    if (recentInput.current && !recentInput.current.reportValidity()) return;
    if (dividendInput.current && !dividendInput.current.reportValidity())
      return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const job = await api<ScreeningJob>("/screening/run", {
        method: "POST",
        body: JSON.stringify(definition()),
      });
      setQuery({ run: job.id });
      setHistoryRevision((value) => value + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : "筛选失败");
    } finally {
      setBusy(false);
    }
  }
  async function save() {
    if (recentInput.current && !recentInput.current.reportValidity()) return;
    if (dividendInput.current && !dividendInput.current.reportValidity())
      return;
    setError("");
    try {
      await api("/screening/presets", {
        method: "POST",
        body: JSON.stringify({ name: presetName, definition: definition() }),
      });
      presets.reload();
      setNotice("筛选方案已保存");
      setPresetName("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    }
  }
  async function cancel() {
    try {
      await api(`/screening/runs/${runId}/cancel`, { method: "POST" });
      task.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "取消失败");
    }
  }
  const selectedRule = rules.data?.rules.find((r) => r.id === ruleId);
  return (
    <>
      <PageTitle
        title="股票筛选"
        subtitle="选择历史交易日，组合条件。新筛选排除已退市证券；历史结果仅涵盖存续证券，存在幸存者偏差。"
      />
      {(task.data?.result?.fixture_mode ?? market.data?.fixture_mode) && (
        <p
          role="status"
          className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900"
        >
          fixture 模式：当前使用合成测试数据。
        </p>
      )}
      <ErrorBanner
        message={
          error || rules.error || market.error || presets.error || task.error
        }
      />
      {notice && (
        <p role="status" className="my-3 text-sm text-primary">
          {notice}
        </p>
      )}
      {task.data && (
        <div className="mt-5 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-white p-4 text-sm">
          <div>
            <span className="badge mr-3">{statusLabels[task.data.status]}</span>
            任务 {task.data.id.slice(0, 8)} ·{" "}
            {Math.round(task.data.progress * 100)}%
            {task.data.status === "queued" && (
              <span className="ml-3 text-muted-foreground">
                等待 Worker 执行
              </span>
            )}
          </div>
          {activeJob(task.data.status) && (
            <Button size="sm" variant="outline" onClick={() => void cancel()}>
              取消筛选
            </Button>
          )}
          <ErrorBanner message={task.data.error ?? ""} />
        </div>
      )}

      <ModuleTabs
        mainLabel="股票筛选"
        resultLabel="筛选结果"
        result={
          <>
            {runId && task.data?.status === "succeeded" && (
              <>
                <div className="mt-4 rounded-lg border border-border p-4">
                  <ScreeningDescription
                    definition={task.data.payload}
                    ruleVersion={task.data.snapshot?._rule_version}
                  />
                </div>
                <ResultsTable key={runId} runId={runId} />
              </>
            )}
            {!runId && <p>请先提交任务或查看历史任务。</p>}
          </>
        }
        history={(active) => (
          <Card className="mt-6">
            <h2 className="border-b border-border p-5 font-semibold">
              历史筛选任务
            </h2>
            <TaskHistory
              kind="screening"
              active={active}
              refreshKey={historyRevision}
              onSelect={(id) => setQuery({ run: id, tab: "result" })}
              onDeleted={(id) => {
                if (runId === id) {
                  loadedRun.current = "";
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
        {!rules.data ? (
          <Loading />
        ) : (
          <Card className="p-6">
            <div className="mb-5 flex items-center gap-2 font-semibold">
              <Filter size={18} />
              筛选条件
            </div>
            <div className="grid gap-4 md:grid-cols-3">
              <label>
                筛选方式
                <select
                  aria-label="筛选方式"
                  value={mode}
                  onChange={(e) => setMode(e.target.value as typeof mode)}
                >
                  <option value="rule">规则筛选</option>
                  <option value="signal">策略信号筛选</option>
                  <option value="rule_signal">规则与信号同时满足</option>
                  <option value="dividend">近期分红筛选</option>
                </select>
              </label>
              {mode !== "signal" && mode !== "dividend" && (
                <label>
                  筛选规则
                  <select
                    aria-label="筛选规则"
                    value={ruleId}
                    onChange={(e) => {
                      const id = e.target.value;
                      dividendInput.current?.setCustomValidity("");
                      setDividendDraft(null);
                      setRuleId(id);
                      setParameters(
                        rules.data!.rules.find((r) => r.id === id)!.defaults,
                      );
                    }}
                  >
                    {rules.data.rules.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.id} · {r.name}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <label>
                筛选日期
                <input
                  aria-label="筛选日期"
                  type="date"
                  max={market.data?.latest_market_date ?? undefined}
                  value={day}
                  onChange={(e) => setDay(e.target.value)}
                />
              </label>
              <label>
                股票池
                <select
                  aria-label="股票池"
                  value={fromWatchlist ? "watchlist" : universe}
                  onChange={(e) => {
                    const value = e.target.value;
                    setUniverse(value === "all" ? "all" : "custom");
                    setFromWatchlist(value === "watchlist");
                    if (value === "watchlist" || value === "all") setCodes("");
                  }}
                >
                  <option value="all">全沪深 A 股</option>
                  <option value="custom">自选股票池</option>
                  <option value="watchlist">从自选列表筛选</option>
                </select>
              </label>
            </div>
            {fromWatchlist && (
              <div className="mt-4">
                <WatchlistPicker
                  onApply={(value) => setCodes(value.join(", "))}
                />
              </div>
            )}
            <div className="mt-4">
              <p className="mb-2 text-sm font-medium">股票名称或代码</p>
              <StockInput
                label={universe === "all" ? "股票名称或代码" : "自选股票代码"}
                multiple
                value={universe === "custom" ? codes : ""}
                onChange={(value) => {
                  setCodes(value);
                  setUniverse("custom");
                  setFromWatchlist(false);
                }}
                asOfDate={day || undefined}
              />
              <p className="mt-2 text-xs text-muted-foreground">
                选择股票后仅在所选股票中筛选，仍需满足下方条件；筛选全市场请将股票池切回全沪深 A 股。
              </p>
            </div>
            <div className="my-5 border-t border-border" />
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {Object.entries(
                mode === "signal" || mode === "dividend" ? {} : parameters,
              )
                .filter(([, v]) => typeof v === "number")
                .map(([key, value]) => {
                  const [label, unit] = parameterLabels[key] ?? [key, ""];
                  const multiplier = unit === "%" ? 100 : 1;
                  const constraint = selectedRule?.schema.properties[key];
                  return (
                    <label key={key}>
                      {label} ({unit})
                      <input
                        aria-label={label}
                        ref={key === "dividend_min" ? dividendInput : undefined}
                        type={key === "dividend_min" ? "text" : "number"}
                        inputMode={
                          key === "dividend_min" ? "decimal" : undefined
                        }
                        required={key === "dividend_min"}
                        pattern={
                          key === "dividend_min"
                            ? "[0-9]+([.][0-9]?)?"
                            : undefined
                        }
                        step={key === "dividend_min" ? "0.1" : "any"}
                        min={
                          constraint?.minimum === undefined
                            ? undefined
                            : constraint.minimum * multiplier
                        }
                        max={
                          constraint?.maximum === undefined
                            ? undefined
                            : constraint.maximum * multiplier
                        }
                        value={
                          key === "dividend_min"
                            ? (dividendDraft ??
                              String(
                                Number(
                                  ((value as number) * multiplier).toFixed(10),
                                ),
                              ))
                            : (value as number) * multiplier
                        }
                        onChange={(e) => {
                          const raw = e.target.value;
                          if (key === "dividend_min") {
                            setDividendDraft(raw);
                            const numeric = Number(raw) / multiplier;
                            const valid =
                              /^[0-9]+(\.[0-9]?)?$/.test(raw) &&
                              (constraint?.minimum === undefined ||
                                numeric >= constraint.minimum) &&
                              (constraint?.maximum === undefined ||
                                numeric <= constraint.maximum);
                            e.target.setCustomValidity(
                              valid ? "" : "请输入范围内的股息率，最多一位小数",
                            );
                            if (!valid) return;
                          }
                          setParameters((p) => ({
                            ...p,
                            [key]: Number(raw) / multiplier,
                          }));
                        }}
                        onBlur={
                          key === "dividend_min"
                            ? (e) => {
                                if (e.target.checkValidity())
                                  setDividendDraft(
                                    String(
                                      Number(Number(e.target.value).toFixed(1)),
                                    ),
                                  );
                              }
                            : undefined
                        }
                      />
                    </label>
                  );
                })}
            </div>
            {mode !== "signal" &&
              mode !== "dividend" &&
              ruleId === "F" &&
              parameters.group && (
                <ConditionEditor
                  group={parameters.group as Group}
                  factors={rules.data.factors}
                  onChange={(group) => setParameters((p) => ({ ...p, group }))}
                />
              )}
            {(mode === "signal" || mode === "rule_signal") && (
              <>
                <label className="mt-4 max-w-xs">
                  信号方向
                  <select
                    aria-label="信号方向"
                    value={direction}
                    onChange={(e) =>
                      setDirection(e.target.value as typeof direction)
                    }
                  >
                    <option value="buy">仅买入</option>
                    <option value="sell">仅卖出</option>
                    <option value="either">买卖任一</option>
                  </select>
                </label>
                <StrategySelection
                  purpose="screening"
                  selected={strategies}
                  onChange={setStrategies}
                />
              </>
            )}
            <div className="mt-5 flex flex-wrap items-end gap-5">
              {mode !== "dividend" && (
                <label className="!flex !items-center !gap-2">
                  <input
                    type="checkbox"
                    aria-label="近期分红"
                    checked={recentEnabled}
                    onChange={(e) => setRecentEnabled(e.target.checked)}
                  />
                  近期分红（同时满足）
                </label>
              )}
              {(mode === "dividend" || recentEnabled) && (
                <label>
                  未来登记窗口（自然日）
                  <input
                    ref={recentInput}
                    aria-label="登记窗口天数"
                    type="number"
                    required
                    min={1}
                    max={365}
                    step={1}
                    className="!w-32"
                    value={Number.isNaN(recentDays) ? "" : recentDays}
                    onChange={(e) => setRecentDays(e.target.valueAsNumber)}
                  />
                </label>
              )}
            </div>
            {(mode === "dividend" || recentEnabled) && (
              <p className="mt-3 text-xs text-muted-foreground">
                筛选日后 {Number.isFinite(recentDays) ? recentDays : "—"}{" "}
                天内登记、筛选日前已公开实施公告的现金分红安排。预计金额为税前参考值。
              </p>
            )}
            <div className="mt-5 flex flex-wrap gap-5">
              {(["exclude_st", "exclude_suspended"] as const).map((key) => (
                <label key={key} className="!flex !items-center !gap-2">
                  <input
                    aria-label={key === "exclude_st" ? "剔除ST" : "剔除停牌"}
                    type="checkbox"
                    checked={
                      (mode === "signal" || mode === "dividend"
                        ? signalParameters
                        : parameters)[key] === true
                    }
                    onChange={(e) =>
                      (mode === "signal" || mode === "dividend"
                        ? setSignalParameters
                        : setParameters)((p) => ({
                        ...p,
                        [key]: e.target.checked,
                      }))
                    }
                  />
                  {key === "exclude_st"
                    ? "剔除 ST（历史状态未知不放行）"
                    : "剔除停牌"}
                </label>
              ))}
            </div>
            <div className="mt-6 flex flex-wrap items-center gap-3 border-t border-border pt-5">
              <Button
                disabled={
                  busy ||
                  !day ||
                  ((mode === "signal" || mode === "rule_signal") &&
                    !strategies.length) ||
                  Boolean(task.data && activeJob(task.data.status))
                }
                onClick={() => void execute()}
              >
                <Filter size={15} />
                执行筛选
              </Button>
              <input
                className="!w-48"
                aria-label="方案名称"
                placeholder="筛选方案名称"
                value={presetName}
                onChange={(e) => setPresetName(e.target.value)}
              />
              <Button
                variant="outline"
                disabled={
                  !presetName.trim() ||
                  !day ||
                  ((mode === "signal" || mode === "rule_signal") &&
                    !strategies.length)
                }
                onClick={() => void save()}
              >
                <Save size={15} />
                保存方案
              </Button>
              <select
                aria-label="已保存方案"
                className="!w-52"
                value=""
                onChange={(e) => {
                  const preset = presets.data?.find(
                    (p) => p.id === e.target.value,
                  );
                  if (preset) {
                    apply(preset.definition);
                    setNotice(`已载入：${preset.name}`);
                  }
                }}
              >
                <option value="">载入已保存方案</option>
                {presets.data?.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>
            <p className="mt-4 text-xs leading-5 text-muted-foreground">
              收益和均线使用截至筛选日的复权价格。股息仅纳入已公开、已实施且已派息事件；上市不足与关键数据缺失不会默认命中。
            </p>
          </Card>
        )}
      </ModuleTabs>
    </>
  );
}
