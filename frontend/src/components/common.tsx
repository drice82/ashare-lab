import { ArrowUpRight, Clock3, LoaderCircle } from "lucide-react";
import { Link } from "react-router-dom";
import { statusLabels } from "../format";
import type { ResearchJob } from "../types";

export function ErrorBanner({ message }: { message?: string }) {
  return message ? (
    <div
      role="alert"
      className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
    >
      {message}
    </div>
  ) : null;
}
export function Loading() {
  return (
    <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
      <LoaderCircle className="h-4 w-4 animate-spin" />
      正在读取本地数据…
    </div>
  );
}
export function PageTitle({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="mb-7 flex flex-wrap items-end justify-between gap-4">
      <div>
        <p className="eyebrow mb-2">A-SHARE RESEARCH WORKSPACE</p>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        <p className="mt-2 text-sm text-muted-foreground">{subtitle}</p>
      </div>
      {action}
    </div>
  );
}
export function TaskTable({
  jobs,
  onSelect,
  onDelete,
  deleting = false,
}: {
  jobs: ResearchJob[];
  onSelect?: (id: string) => void;
  onDelete?: (job: ResearchJob) => void;
  deleting?: boolean;
}) {
  return jobs.length ? (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead>
          <tr>
            <th>任务</th>
            <th>状态</th>
            <th>进度 / 命中</th>
            <th>创建时间</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.id}>
              <td>
                {job.kind === "dca"
                  ? `定投 ${job.payload.stock_code}`
                  : job.kind === "backtest"
                    ? `回测 ${job.payload.stock_code}`
                  : job.kind === "screening"
                    ? `规则 ${job.payload.rule_id} · ${job.payload.as_of_date}`
                    : job.payload.mode === "namechange"
                      ? "历史名称更新"
                      : "市场数据同步"}
                {(job.kind === "dca" || job.kind === "backtest") && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    {job.payload.start_date} 至 {job.payload.end_date}
                  </p>
                )}
                <p className="mt-1 font-mono text-[10px] text-muted-foreground">
                  {job.id.slice(0, 8)}
                </p>
              </td>
              <td>
                <span
                  className={`badge ${job.status === "failed" ? "!bg-red-50 !text-red-700" : ""}`}
                >
                  {statusLabels[job.status] ?? job.status}
                </span>
              </td>
              <td>
                {job.kind === "screening" && job.result
                  ? `${job.result.matched} / ${job.result.total}`
                  : `${Math.round(job.progress * 100)}%`}
              </td>
              <td className="text-muted-foreground">
                {new Date(job.created_at).toLocaleString("zh-CN")}
              </td>
              <td>
                {onSelect ? (
                  <button
                    onClick={() => onSelect(job.id)}
                    className="text-primary hover:underline"
                  >
                    {job.kind === "sync" ? "查看日志" : "查看"}
                  </button>
                ) : (
                  <Link
                    to={`/${job.kind === "backtest" ? "backtest" : job.kind === "dca" ? "dca" : "screening"}?run=${job.id}`}
                    className="inline-flex items-center gap-1 text-primary"
                  >
                    查看
                    <ArrowUpRight size={14} />
                  </Link>
                )}
                {onDelete && <button className="ml-4 text-red-700 disabled:opacity-40"
                  aria-label={`删除任务 ${job.id}`} disabled={deleting || !["succeeded", "failed", "cancelled", "partial"].includes(job.status)}
                  title={["succeeded", "failed", "cancelled", "partial"].includes(job.status) ? "永久删除任务" : "请先取消任务，等待任务结束后再删除"}
                  onClick={() => onDelete(job)}>删除</button>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  ) : (
    <div className="flex items-center gap-2 p-8 text-sm text-muted-foreground">
      <Clock3 size={16} />
      暂无任务，开始第一次研究。
    </div>
  );
}
