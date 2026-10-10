import { TaskHistory } from "../components/TaskHistory";
import { useEffect, useState } from "react";
import { Database, RefreshCw } from "lucide-react";
import { api as requestApi, useResource as resource } from "../api";
import {
  ErrorBanner,
  Loading,
  PageTitle,
} from "../components/common";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { activeJob, statusLabels } from "../format";
import type { Job, MarketStatus } from "../types";

const datasetNames: Record<string, string> = {
  stock_basic: "股票基础信息",
  namechange: "历史名称与 ST",
  trade_cal: "交易日历",
  daily: "未复权日线",
  adj_factor: "复权因子",
  daily_basic: "每日基本指标",
  dividend: "分红事件",
  stk_limit: "涨跌停价格",
  suspend_d: "停复牌事件",
};
export default function DataManagement() {
  const [password, setPassword] = useState("");
  const [input, setInput] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const lock = () => {
      setPassword("");
      setInput("");
      setError("数据管理认证已失效，请重新输入密码");
    };
    window.addEventListener("ashare-data-locked", lock);
    return () => window.removeEventListener("ashare-data-locked", lock);
  }, []);
  if (password) return <Management password={password} />;
  return <>
    <PageTitle title="数据管理" subtitle="请输入独立的数据管理密码。离开页面或刷新后需要重新输入。" />
    <ErrorBanner message={error} />
    <Card className="p-6 max-w-md">
      <form onSubmit={(event) => {
        event.preventDefault();
        setBusy(true);
        setError("");
        const value = input;
        void requestApi("/data/status", {}, value)
          .then(() => { setPassword(value); setInput(""); })
          .catch((e: unknown) => setError(e instanceof Error ? e.message : "解锁失败"))
          .finally(() => setBusy(false));
      }}>
        <label>
          数据管理密码
          <input aria-label="数据管理密码" type="password" autoComplete="off"
            value={input} onChange={(e) => setInput(e.target.value)} />
        </label>
        <Button className="mt-4" disabled={busy || !input} type="submit">解锁数据管理</Button>
      </form>
    </Card>
  </>;
}

function Management({ password }: { password: string }) {
  function useResource<T>(path: string | null, pollMs = 0) {
    return resource<T>(path, pollMs, password);
  }
  function api<T>(path: string, options: RequestInit = {}) {
    return requestApi<T>(path, options, password);
  }
  const status = useResource<MarketStatus>("/data/status", 15000);
  const [historyRevision, setHistoryRevision] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const task = useResource<Job>(
    selected ? `/data/sync/${selected}` : null,
    2000,
  );
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [start, setStart] = useState(() => {
    const d = new Date();
    d.setFullYear(d.getFullYear() - 3);
    return d.toISOString().slice(0, 10);
  });
  const [end, setEnd] = useState(() =>
    new Date(Date.now() - 86400000).toISOString().slice(0, 10),
  );
  async function sync(mode: string, historyRefresh = "incremental") {
    setBusy(true);
    setError("");
    try {
      const job = await api<Job>("/data/sync", {
        method: "POST",
        body: JSON.stringify({
          mode,
          ...(mode === "dividend" || mode === "namechange" ? { history_refresh: historyRefresh } : {}),
          ...(mode === "initialize" ? { start_date: start } : {}),
          ...((mode === "initialize" || mode === "incremental") ? { end_date: end } : {}),
        }),
      });
      setSelected(job.id);
      setHistoryRevision((value) => value + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : "同步提交失败");
    } finally {
      setBusy(false);
    }
  }
  async function action(name: string) {
    try {
      await api(`/data/sync/${selected}/${name}`, { method: "POST" });
      task.reload();
      setHistoryRevision((value) => value + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : "任务操作失败");
    }
  }
  const unavailable =
    !status.data?.token_configured || status.data.fixture_mode;
  return (
    <>
      <PageTitle
        title="数据管理"
        subtitle="将数据同步到本地仓库，查看覆盖范围、版本与任务日志。"
      />
      <ErrorBanner
        message={error || status.error || task.error}
      />
      <Card className="mb-6 p-6">
        <div className="mb-5 flex items-center gap-2 font-semibold">
          <Database size={18} />
          历史初始化与增量更新
        </div>
        <div className="grid items-end gap-4 sm:grid-cols-4">
          <label>
            开始日期
            <input
              aria-label="开始日期"
              type="date"
              value={start}
              onChange={(e) => setStart(e.target.value)}
            />
          </label>
          <label>
            结束日期
            <input
              aria-label="结束日期"
              type="date"
              value={end}
              onChange={(e) => setEnd(e.target.value)}
            />
          </label>
          <Button
            disabled={busy || unavailable || !start || !end}
            onClick={() => void sync("initialize")}
          >
            初始化历史数据
          </Button>
          <Button
            variant="outline"
            disabled={busy || unavailable || !end}
            onClick={() => void sync("incremental")}
          >
            <RefreshCw size={15} />
            增量同步
          </Button>
        </div>
        <p className="mt-4 text-xs text-muted-foreground">
          {status.data?.fixture_mode
            ? "固定测试环境不可混入真实数据；真实同步请使用独立数据目录。"
            : !status.data?.token_configured
              ? "未配置 TUSHARE_TOKEN。请在服务器 .env 中配置并重建 API 和 Worker。"
              : "初始化自动补充120天指标预热数据并获取分红历史；增量同步不获取分红。分红每天北京时间01:00更新，历史名称每天02:00更新；两者每月全量校正。请求并发数和总限速以服务器配置为准。"}
        </p>
      </Card>
      <Card className="mb-6 p-6">
        <h2 className="mb-3 font-semibold">收盘后每日增量同步</h2>
        <p className="text-sm">北京时间 · {status.data?.incremental_schedule?.schedule ?? "每天 16:00"}</p>
        <p className="mt-2 text-sm text-muted-foreground">
          自动调度：{status.data?.incremental_schedule?.enabled ? "已启用（Worker 需保持运行）" : "未启用（需真实行情初始化与 Token）"}
          {" · "}最近完成：{status.data?.incremental_schedule?.last_completed
            ? new Date(status.data.incremental_schedule.last_completed).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" }) : "暂无增量任务完成"}
        </p>
        {status.data?.incremental_schedule && <p className="mt-2 text-sm text-muted-foreground">
          下次计划：{new Date(status.data.incremental_schedule.next_run).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}
          {" · "}最近任务：{status.data.incremental_schedule.latest_job ? statusLabels[status.data.incremental_schedule.latest_job.status] : "暂无"}
        </p>}
        <p className="mt-3 text-xs text-muted-foreground">
          {status.data?.incremental_schedule?.limitation ?? "增量同步不包含分红与历史名称。"}
          到时入队并同步至当天；执行等待现有任务完成，停机后仅补交最近到期的一次。
        </p>
      </Card>
      <Card className="mb-6 p-6">
        <h2 className="mb-3 font-semibold">分红每日更新</h2>
        <p className="text-sm">北京时间 · {status.data?.dividend_schedule?.schedule ?? "每天 01:00"}</p>
        <p className="mt-2 text-sm text-muted-foreground">
          自动调度：{status.data?.dividend_schedule?.enabled ? "已启用（Worker 需保持运行）" : "未启用"}
          {" · "}最近完成：{status.data?.dividend_schedule?.last_completed
            ? new Date(status.data.dividend_schedule.last_completed).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" }) : "暂无独立分红任务完成"}
        </p>
        {status.data?.dividend_schedule && <p className="mt-2 text-sm text-muted-foreground">
          下次计划：{new Date(status.data.dividend_schedule.next_run).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}
          {" · "}最近任务：{status.data.dividend_schedule.latest_job ? statusLabels[status.data.dividend_schedule.latest_job.status] : "暂无"}
        </p>}
        <p className="mt-3 text-xs text-muted-foreground">
          {status.data?.dividend_schedule?.limitation ?? "已有分红继续使用，未确认事件保持未知。"}
          每日包含周末与节假日；到时入队，实际执行等待现有任务完成。失败或部分完成可在任务列表中从断点恢复。
        </p>
        <Button className="mt-4" disabled={busy || unavailable} onClick={() => void sync("dividend")}>立即更新分红</Button>
        <Button className="mt-4 ml-3" variant="outline" disabled={busy || unavailable} onClick={() => void sync("dividend", "full")}>全量校正分红</Button>
        <p className="mt-3 text-xs">全量计划：{status.data?.dividend_full_schedule?.schedule ?? "每月第一个星期六 03:00"} · 最近全量覆盖：{status.data?.dividend_schedule?.last_full_coverage ?? "暂无"} · 下次全量：{status.data?.dividend_full_schedule?.next_run ?? "暂无"}</p>
      </Card>
      <Card className="mb-6 p-6">
        <h2 className="mb-3 font-semibold">历史名称每日更新</h2>
        <p className="text-sm">北京时间 · {status.data?.namechange_schedule?.schedule ?? "每天 02:00"}</p>
        <p className="mt-2 text-sm text-muted-foreground">
          自动调度：{status.data?.namechange_schedule?.enabled ? "已启用（Worker 需保持运行）" : "未启用"}
          {" · "}最近完成：{status.data?.namechange_schedule?.last_completed
            ? new Date(status.data.namechange_schedule.last_completed).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" }) : "暂无独立名称任务完成"}
        </p>
        {status.data?.namechange_schedule && <p className="mt-2 text-sm text-muted-foreground">
          下次计划：{new Date(status.data.namechange_schedule.next_run).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}
          {" · "}最近任务：{status.data.namechange_schedule.latest_job ? statusLabels[status.data.namechange_schedule.latest_job.status] : "暂无"}
        </p>}
        <p className="mt-3 text-xs text-muted-foreground">
          {status.data?.namechange_schedule?.limitation ?? "回查30天公告，每月全量校正。"}
          已有名称继续使用；无名称记录时状态为未知。已退市证券不再逐股更新名称和分红。
        </p>
        <Button className="mt-4" disabled={busy || unavailable} onClick={() => void sync("namechange")}>立即更新历史名称</Button>
        <Button className="mt-4 ml-3" variant="outline" disabled={busy || unavailable} onClick={() => void sync("namechange", "full")}>全量校正历史名称</Button>
        <p className="mt-3 text-xs">全量计划：{status.data?.namechange_full_schedule?.schedule ?? "每月第一个星期六 05:00"} · 最近全量覆盖：{status.data?.namechange_schedule?.last_full_coverage ?? "暂无"} · 下次全量：{status.data?.namechange_full_schedule?.next_run ?? "暂无"}</p>
      </Card>
      <Card className="mb-6">
        <h2 className="border-b border-border p-5 font-semibold">本地数据集</h2>
        {!status.data && !status.error ? (
          <Loading />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr>
                  <th>数据集</th>
                  <th>行数</th>
                  <th>覆盖日期</th>
                  <th>来源</th>
                  <th>更新时间</th>
                </tr>
              </thead>
              <tbody>
                {status.data?.datasets.map((d) => (
                  <tr key={d.dataset}>
                    <td>
                      {datasetNames[d.dataset] ?? d.dataset}
                      <p
                        title={d.version_id}
                        className="mt-1 font-mono text-[10px] text-muted-foreground"
                      >
                        {d.dataset} · {d.version_id.slice(0, 8)}
                      </p>
                    </td>
                    <td>{d.rows.toLocaleString()}</td>
                    <td>
                      {d.start_date ?? "—"} ~ {d.end_date ?? "—"}
                    </td>
                    <td>
                      <span className="badge">
                        {d.source === "fixture" ? "固定测试数据" : d.source}
                      </span>
                    </td>
                    <td>{new Date(d.updated_at).toLocaleString("zh-CN")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {status.data && !status.data.datasets.length && (
              <p className="p-8 text-sm text-muted-foreground">
                尚无本地数据，请先初始化。
              </p>
            )}
          </div>
        )}
        <div className="p-4 text-xs text-muted-foreground">
          行情交易日缺口：
          {status.data?.missing_trade_dates.join("、") || "无已识别日期缺口"}
          。停牌和股票级缺失在筛选解释中单独显示。
        </div>
      </Card>
      <Card>
        <h2 className="border-b border-border p-5 font-semibold">同步任务</h2>
        <TaskHistory kind="sync" password={password} refreshKey={historyRevision} onSelect={setSelected}
          onDeleted={(id) => {if (selected === id) setSelected(null);status.reload();}} />
      </Card>
      {task.data && (
        <Card className="mt-6 p-5">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <h2 className="font-semibold">
              任务日志 · {statusLabels[task.data.status]}
            </h2>
            <div className="flex gap-2">
              {activeJob(task.data.status) && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void action("cancel")}
                >
                  取消任务
                </Button>
              )}
              {["failed", "partial", "cancelled"].includes(
                task.data.status,
              ) && (
                <Button size="sm" onClick={() => void action("resume")}>
                  从断点恢复
                </Button>
              )}
            </div>
          </div>
          <div className="h-1.5 overflow-hidden rounded bg-muted">
            <div
              className="h-full bg-primary"
              style={{ width: `${task.data.progress * 100}%` }}
            />
          </div>
          <ErrorBanner message={task.data.error ?? ""} />
          <div className="mt-4 max-h-64 overflow-auto rounded-lg bg-[#142b22] p-4 font-mono text-xs leading-6 text-[#d0dfd0]">
            {task.data.logs?.length
              ? task.data.logs.map((log, i) => (
                  <div key={i}>
                    [{log.level}] {new Date(log.at).toLocaleTimeString("zh-CN")}{" "}
                    {log.message}
                  </div>
                ))
              : "等待 Worker 执行。请确保 Worker 已启动。"}
          </div>
        </Card>
      )}
    </>
  );
}
