import { useEffect, useState } from "react";
import { Plus, Save } from "lucide-react";
import { useResource } from "../api";
import type { MarketStatus } from "../types";
import { useUserSession } from "../userSession";
import {
  useWatchlists,
  type Watchlist,
  type WatchlistSource,
} from "../watchlists";
import StockInput from "../components/StockInput";
import { ErrorBanner, PageTitle } from "../components/common";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";

export default function Watchlists() {
  const user = useUserSession();
  const lists = useWatchlists();
  const market = useResource<MarketStatus>("/market/status");
  const [source, setSource] = useState<WatchlistSource>("local");
  const [editing, setEditing] = useState(false);
  const [selected, setSelected] = useState<Watchlist>();
  const [name, setName] = useState("");
  const [codes, setCodes] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const rows = source === "account" ? lists.account : lists.local;
  function reset() {
    setEditing(false);
    setSelected(undefined);
    setName("");
    setCodes("");
    setConfirmDelete(false);
    setNotice("");
    setError("");
  }
  useEffect(() => {
    setSource(user.session ? "account" : "local");
    reset();
  }, [user.session?.owner_id]);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(""), 4000);
    return () => clearTimeout(timer);
  }, [notice]);
  function edit(row?: Watchlist) {
    setSelected(row);
    setName(row?.name ?? "");
    setCodes(row?.codes.join(", ") ?? "");
    setEditing(true);
    setConfirmDelete(false);
    setError("");
    setNotice("");
  }
  async function save() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const row = await lists.save(
        source,
        { name, codes: codes.split(/[,，;；\s]+/).filter(Boolean) },
        selected,
      );
      setSelected(row);
      setName(row.name);
      setCodes(row.codes.join(", "));
      setNotice("列表已保存");
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }
  async function remove() {
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      await lists.remove(source, selected);
      reset();
      setNotice("列表已删除");
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PageTitle
        title="自选列表"
        subtitle="多个列表 · 手动维护 · 筛选与组合回测调用"
      />
      <Card className="mb-5 p-5 space-y-3">
        {user.session ? (
          <p className="text-sm">
            已登录：{user.session.email} ·
            账号列表跨设备保存，本机列表独立保留。
          </p>
        ) : (
          <p className="text-sm text-muted-foreground">
            当前列表保存在本机；登录后可维护账号自选列表并跨设备使用。{" "}
            <Button variant="ghost" size="sm" onClick={user.openLogin}>
              登录
            </Button>
          </p>
        )}
        {user.status === "loading" && (
          <p className="text-xs">正在查询登录状态…</p>
        )}
        <ErrorBanner message={user.error} />
        {user.status === "error" && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => void user.refresh()}
          >
            重试登录状态
          </Button>
        )}
        {user.session && (
          <div className="flex gap-2" role="group" aria-label="列表保存位置">
            {(["account", "local"] as const).map((value) => (
              <Button
                key={value}
                variant={source === value ? "default" : "outline"}
                aria-pressed={source === value}
                disabled={busy}
                onClick={() => {
                  reset();
                  setSource(value);
                }}
              >
                {value === "account" ? "账号列表" : "本机列表"}
              </Button>
            ))}
          </div>
        )}
        <p className="text-xs text-muted-foreground">
          列表仅保存股票代码；研究使用所选交易日和固定数据版本，历史筛选存在幸存者偏差。
        </p>
        {market.data?.fixture_mode && (
          <p className="text-xs">
            测试数据模式（fixture），股票联想来自隔离测试数据。
          </p>
        )}
      </Card>
      <ErrorBanner message={error} />
      <ErrorBanner
        message={source === "local" ? lists.localError : lists.accountError}
      />
      {notice && (
        <p role="status" className="mb-3 text-sm text-primary">
          {notice}
        </p>
      )}
      <div className="grid gap-5 lg:grid-cols-[minmax(220px,1fr)_minmax(0,2fr)]">
        <Card className="p-5 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2>
              {source === "account" ? "账号列表" : "本机列表"} · {rows.length}
            </h2>
            <Button
              size="sm"
              disabled={busy || (source === "account" && !user.session)}
              onClick={() => edit()}
            >
              <Plus size={14} />
              新建列表
            </Button>
          </div>
          <Button
            variant="ghost"
            size="sm"
            disabled={busy || lists.loading}
            onClick={() => void lists.reload()}
          >
            刷新列表
          </Button>
          {source === "account" && lists.loading && (
            <p className="text-xs">正在读取账号列表…</p>
          )}
          {!rows.length && (
            <p className="text-sm text-muted-foreground">
              还没有列表，点击“新建列表”开始。
            </p>
          )}
          {rows.map((row) => (
            <button
              key={row.id}
              type="button"
              disabled={busy}
              aria-pressed={selected?.id === row.id}
              onClick={() => edit(row)}
              className={`w-full rounded-lg border px-3 py-3 text-left text-sm break-all ${selected?.id === row.id ? "border-primary bg-muted" : "border-border"}`}
            >
              {row.name} · {row.codes.length}只
            </button>
          ))}
        </Card>
        <Card className="min-w-0 p-5">
          {editing ? (
            <div className="space-y-4">
              <h2>{selected ? "编辑列表" : "新建列表"}</h2>
              <fieldset disabled={busy} className="min-w-0 space-y-4">
                <label>
                  列表名称
                  <input
                    maxLength={80}
                    aria-label="列表名称"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                  />
                </label>
                <label>
                  列表股票
                  <StockInput
                    label="列表股票"
                    multiple
                    value={codes}
                    onChange={setCodes}
                    asOfDate={market.data?.latest_market_date ?? undefined}
                    dataVersion={
                      market.data?.datasets?.find(
                        (dataset) => dataset.dataset === "stock_basic",
                      )?.version_id
                    }
                  />
                </label>
                <p className="text-xs text-muted-foreground">
                  可以输入名称或拼音，也可粘贴带 .SH／.SZ
                  的股票代码。允许先保存空列表。
                </p>
                <div className="flex flex-wrap gap-2">
                  <Button onClick={() => void save()} disabled={!name.trim()}>
                    <Save size={14} />
                    保存列表
                  </Button>
                  <Button variant="outline" onClick={reset}>
                    取消编辑
                  </Button>
                  {selected && (
                    <Button
                      variant="destructive"
                      onClick={() => setConfirmDelete(true)}
                    >
                      删除列表
                    </Button>
                  )}
                </div>
              </fieldset>
              {confirmDelete && (
                <div
                  role="alertdialog"
                  aria-label="删除自选列表确认"
                  className="rounded-lg border p-4 space-y-3"
                >
                  <p className="text-sm">
                    确认删除“{selected?.name}”？已经提交的研究任务不受影响。
                  </p>
                  <div className="flex gap-2">
                    <Button
                      variant="destructive"
                      disabled={busy}
                      onClick={() => void remove()}
                    >
                      确认删除
                    </Button>
                    <Button
                      variant="outline"
                      disabled={busy}
                      onClick={() => setConfirmDelete(false)}
                    >
                      取消删除
                    </Button>
                  </div>
                </div>
              )}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              选择一个列表编辑，或新建列表。
            </p>
          )}
        </Card>
      </div>
    </>
  );
}
