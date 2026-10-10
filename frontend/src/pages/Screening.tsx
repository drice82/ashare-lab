import { TaskHistory } from "../components/TaskHistory";
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Filter, Save } from "lucide-react";
import { api, useResource } from "../api";
import { activeJob, statusLabels } from "../format";
import { ConditionEditor } from "../components/ConditionEditor";
import StockInput from "../components/StockInput";
import ResultsTable from "../components/ResultsTable";
import { ErrorBanner, Loading, PageTitle } from "../components/common";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import type {
  Factor,
  Group,
  Job,
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
  const task = useResource<Job>(
    runId ? `/screening/runs/${runId}` : null,
    2000,
  );
  const [ruleId, setRuleId] = useState("A");
  const [parameters, setParameters] = useState<Parameters>({});
  const [day, setDay] = useState("");
  const [universe, setUniverse] = useState<"all" | "custom">("all");
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
    setRuleId(definition.rule_id);
    setParameters(definition.parameters);
    setDay(definition.as_of_date);
    setUniverse(definition.universe.type);
    setCodes(definition.universe.codes.join(", "));
  }
  useEffect(() => {
    if (task.data && task.data.id !== loadedRun.current) {
      loadedRun.current = task.data.id;
      apply(task.data.payload);
    }
  }, [task.data]);
  function definition(): ScreeningDefinition {
    return {
      as_of_date: day,
      rule_id: ruleId,
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
      parameters,
    };
  }
  async function execute() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const job = await api<Job>("/screening/run", {
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
        subtitle="选择历史交易日，组合条件。新筛选排除数据版本中已退市证券；历史结果仅涵盖存续证券，存在幸存者偏差。"
      />
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
              筛选规则
              <select
                aria-label="筛选规则"
                value={ruleId}
                onChange={(e) => {
                  const id = e.target.value;
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
                value={universe}
                onChange={(e) =>
                  setUniverse(e.target.value as "all" | "custom")
                }
              >
                <option value="all">全沪深 A 股</option>
                <option value="custom">自选股票池</option>
              </select>
            </label>
          </div>
          {universe === "custom" && (
            <label className="mt-4">
              自选股票
              <StockInput
                label="自选股票代码"
                multiple
                value={codes}
                onChange={setCodes}
                asOfDate={day || undefined}
              />
            </label>
          )}
          <div className="my-5 border-t border-border" />
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {Object.entries(parameters)
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
                      type="number"
                      step="any"
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
                      value={(value as number) * multiplier}
                      onChange={(e) =>
                        setParameters((p) => ({
                          ...p,
                          [key]: Number(e.target.value) / multiplier,
                        }))
                      }
                    />
                  </label>
                );
              })}
          </div>
          {ruleId === "F" && parameters.group && (
            <ConditionEditor
              group={parameters.group as Group}
              factors={rules.data.factors}
              onChange={(group) => setParameters((p) => ({ ...p, group }))}
            />
          )}
          <div className="mt-5 flex flex-wrap gap-5">
            {(["exclude_st", "exclude_suspended"] as const).map((key) => (
              <label key={key} className="!flex !items-center !gap-2">
                <input
                  aria-label={key === "exclude_st" ? "剔除ST" : "剔除停牌"}
                  type="checkbox"
                  checked={parameters[key] === true}
                  onChange={(e) =>
                    setParameters((p) => ({ ...p, [key]: e.target.checked }))
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
              disabled={!presetName.trim() || !day}
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
      {runId && task.data?.status === "succeeded" && (
        <ResultsTable key={runId} runId={runId} />
      )}
      <Card className="mt-6">
        <h2 className="border-b border-border p-5 font-semibold">历史筛选任务</h2>
        <TaskHistory kind="screening" refreshKey={historyRevision} onSelect={(id) => setQuery({run: id})}
          onDeleted={(id) => {if (runId === id) {loadedRun.current = "";setQuery((params) => {params.delete("run");return params;});}}} />
      </Card>
    </>
  );
}
