import { useEffect, useRef, useState } from "react";
import { api, useResource } from "../api";
import type { ResearchJob } from "../types";
import { ErrorBanner, TaskTable } from "./common";
import { Button } from "./ui/button";

export function DeletableTasks({
  jobs,
  kind,
  password,
  onSelect,
  onDeleted,
  onCleared,
}: {
  kind?: ResearchJob["kind"];
  jobs: ResearchJob[];
  password?: string;
  onSelect?: (id: string) => void;
  onDeleted: (id: string) => void;
  onCleared?: () => void;
}) {
  const [target, setTarget] = useState<ResearchJob | "all" | null>(null);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  async function remove() {
    if (!target || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      const selectedKind = target === "all" ? kind! : target.kind;
      const base =
        selectedKind === "sync" ? "/data/sync" : `/${selectedKind}/runs`;
      const path = target === "all" ? base : `${base}/${target.id}`;
      const result = await api<{ ids?: string[]; skipped_active?: number }>(
        path,
        { method: "DELETE" },
        password,
      );
      setTarget(null);
      if (target === "all") {
        result.ids?.forEach(onDeleted);
        onCleared?.();
        setNotice(
          `已删除 ${result.ids?.length ?? 0} 个历史任务，保留了 ${result.skipped_active ?? 0} 个活动任务。`,
        );
      } else {
        onDeleted(target.id);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  return (
    <>
      {kind && (
        <div className="mb-3 flex justify-end">
          <Button
            variant="outline"
            disabled={busy || !jobs.length}
            onClick={() => {
              setError("");
              setNotice("");
              setTarget("all");
            }}
          >
            删除全部历史任务
          </Button>
        </div>
      )}
      {notice && (
        <p role="status" className="mb-3 text-sm text-muted-foreground">
          {notice}
        </p>
      )}
      <TaskTable
        jobs={jobs}
        onSelect={onSelect}
        onDelete={(job) => {
          setError("");
          setTarget(job);
        }}
        deleting={busy}
      />
      {target && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="delete-task-title"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4"
        >
          <div className="w-full max-w-md rounded-lg bg-white p-6 shadow-lg">
            <h2 id="delete-task-title" className="font-semibold">
              {target === "all"
                ? "删除全部历史任务？"
                : `删除任务 ${target.id.slice(0, 8)}？`}
            </h2>
            <p className="my-4 text-sm">
              {target === "all"
                ? "当前模块所有分页中的已结束任务记录、日志和研究结果将永久删除；活动任务保留。"
                : "任务记录、日志和研究结果将永久删除。"}
              已发布行情和数据版本保留。
            </p>
            <ErrorBanner message={error} />
            <div className="mt-4 flex justify-end gap-3">
              <Button
                autoFocus
                variant="outline"
                disabled={busy}
                onClick={() => setTarget(null)}
              >
                保留任务
              </Button>
              <Button disabled={busy} onClick={() => void remove()}>
                {busy
                  ? "正在删除…"
                  : target === "all"
                    ? "确认删除全部"
                    : "确认删除"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

export function TaskHistory({
  kind,
  password,
  onSelect,
  onDeleted,
  refreshKey = 0,
  active = true,
}: {
  kind: ResearchJob["kind"];
  password?: string;
  onSelect?: (id: string) => void;
  onDeleted?: (id: string) => void;
  refreshKey?: number;
  active?: boolean;
}) {
  const [page, setPage] = useState(0);
  const tasks = useResource<ResearchJob[]>(
    active ? `/jobs?kind=${kind}&limit=20&offset=${page * 20}` : null,
    5000,
    password,
  );
  const previousRefresh = useRef(refreshKey);
  useEffect(() => {
    if (previousRefresh.current !== refreshKey) {
      previousRefresh.current = refreshKey;
      tasks.reload();
    }
  }, [refreshKey, tasks.reload]);
  useEffect(() => {
    if (page > 0 && tasks.data?.length === 0) setPage((value) => value - 1);
  }, [page, tasks.data]);
  return (
    <>
      <ErrorBanner message={tasks.error} />
      <DeletableTasks
        kind={kind}
        jobs={tasks.data ?? []}
        password={password}
        onSelect={onSelect}
        onCleared={() => {
          setPage(0);
          tasks.reload();
        }}
        onDeleted={(id) => {
          onDeleted?.(id);
          tasks.reload();
        }}
      />
      <div className="flex items-center justify-end gap-3 p-4 text-sm">
        <Button
          variant="outline"
          size="sm"
          disabled={page === 0}
          onClick={() => setPage(page - 1)}
        >
          上一页
        </Button>
        <span>第 {page + 1} 页</span>
        <Button
          variant="outline"
          size="sm"
          disabled={!tasks.data || tasks.data.length < 20}
          onClick={() => setPage(page + 1)}
        >
          下一页
        </Button>
      </div>
    </>
  );
}
