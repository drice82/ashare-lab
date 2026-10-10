import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { ArrowDownUp, Download } from "lucide-react";
import { exportCsv, useResource } from "../api";
import {
  formatFactor,
  percentFactors,
  statusLabels,
  factorClass,
} from "../format";
import type { Results, ScreeningRow } from "../types";
import { Button } from "./ui/button";
import { Card } from "./ui/card";
import StockInput from "./StockInput";
import { ErrorBanner, Loading } from "./common";

const factorColumns = [
  ["close", "收盘价"],
  ["daily_return", "当日涨跌幅"],
  ["dividend_yield_ttm", "股息率"],
  ["ma20", "MA20"],
  ["ma60", "MA60"],
  ["return_20d", "20日收益"],
  ["return_60d", "60日收益"],
  ["volume_ratio_5d", "5日量比"],
  ["volume_ratio_20d", "20日量比"],
  ["price_distance_ma20", "MA20偏离"],
];
export default function ResultsTable({ runId }: { runId: string }) {
  const [sorting, setSorting] = useState<SortingState>([
    { id: "stock_code", desc: false },
  ]);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("matched");
  const [error, setError] = useState("");
  const [filterFactor, setFilterFactor] = useState("");
  const [minimum, setMinimum] = useState("");
  const [maximum, setMaximum] = useState("");
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
    search,
    match_status: status,
    sort_by: sorting[0]?.id ?? "stock_code",
    order: sorting[0]?.desc ? "desc" : "asc",
  });
  if (filterFactor) {
    params.set("filter_factor", filterFactor);
    const multiplier = percentFactors.has(filterFactor) ? 100 : 1;
    if (minimum !== "")
      params.set("min_value", String(Number(minimum) / multiplier));
    if (maximum !== "")
      params.set("max_value", String(Number(maximum) / multiplier));
  }
  const resource = useResource<Results>(
    `/screening/runs/${runId}/results?${params}`,
  );
  const columns = useMemo<ColumnDef<ScreeningRow>[]>(
    () => [
      {
        id: "stock_code",
        accessorKey: "stock_code",
        header: "股票代码",
        cell: (info) => (
          <Link
            className="font-mono font-medium text-primary hover:underline"
            to={`/stock/${info.row.original.stock_code}?run=${runId}`}
          >
            {info.row.original.stock_code}
          </Link>
        ),
      },
      { id: "stock_name", accessorKey: "stock_name", header: "股票名称" },
      {
        id: "latest_trade_date",
        accessorKey: "latest_trade_date",
        header: "有效行情日",
        cell: (info) => String(info.getValue() ?? "—"),
      },
      ...factorColumns.map(
        ([id, label]): ColumnDef<ScreeningRow> => ({
          id,
          accessorFn: (row) => row.factor_values[id],
          header: label,
          cell: (info) => {
            const value = info.row.original.factor_values[id];
            return (
              <span className={factorClass(id, value)}>
                {formatFactor(id, value)}
              </span>
            );
          },
        }),
      ),
      {
        id: "rule_id",
        accessorKey: "rule_id",
        header: "规则",
        enableSorting: false,
      },
      {
        id: "match_status",
        accessorKey: "match_status",
        header: "结果",
        enableSorting: false,
        cell: (info) => (
          <span className="badge">{statusLabels[String(info.getValue())]}</span>
        ),
      },
    ],
    [runId],
  );
  const table = useReactTable({
    data: resource.data?.items ?? [],
    columns,
    getCoreRowModel: getCoreRowModel(),
    manualSorting: true,
    state: { sorting },
    onSortingChange: (update) => {
      setSorting(update);
      setPage(1);
    },
  });
  async function download() {
    try {
      await exportCsv(runId);
    } catch (e) {
      setError(e instanceof Error ? e.message : "导出失败");
    }
  }
  return (
    <Card className="mt-6">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border p-5">
        <div>
          <h2 className="font-semibold">
            筛选结果{" "}
            <span className="ml-2 rounded bg-[#e8f0e5] px-2 py-1 text-xs text-primary">
              {resource.data?.summary.matched ?? "—"} 只命中
            </span>
          </h2>
          <p className="mt-2 text-xs text-muted-foreground">
            缺失值显示 —；数据不足{" "}
            {resource.data?.summary.insufficient_data ?? "—"}{" "}
            只。点击股票查看图表与触发数值。
          </p>
        </div>
        <Button variant="outline" onClick={() => void download()}>
          <Download size={15} />
          导出 CSV
        </Button>
      </div>
      <div className="flex flex-wrap gap-3 p-4">
        <StockInput
          label="结果搜索"
          className="w-56"
          searchOnly
          value={search}
          onChange={(value) => {
            setSearch(value);
            setPage(1);
          }}
        />
        <select
          aria-label="结果状态"
          className="!w-40"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setPage(1);
          }}
        >
          <option value="matched">仅命中</option>
          <option value="insufficient_data">数据不足</option>
          <option value="not_matched">未命中</option>
          <option value="all">全部股票</option>
        </select>
        <select
          aria-label="数值筛选列"
          className="!w-40"
          value={filterFactor}
          onChange={(e) => {
            setFilterFactor(e.target.value);
            setMinimum("");
            setMaximum("");
            setPage(1);
          }}
        >
          <option value="">数值列筛选</option>
          {factorColumns.map(([id, label]) => (
            <option value={id} key={id}>
              {label}
              {percentFactors.has(id) ? " (%)" : ""}
            </option>
          ))}
        </select>
        {filterFactor && (
          <>
            <input
              type="number"
              step="any"
              aria-label="数值下限"
              className="!w-28"
              placeholder="下限"
              value={minimum}
              onChange={(e) => {
                setMinimum(e.target.value);
                setPage(1);
              }}
            />
            <input
              type="number"
              step="any"
              aria-label="数值上限"
              className="!w-28"
              placeholder="上限"
              value={maximum}
              onChange={(e) => {
                setMaximum(e.target.value);
                setPage(1);
              }}
            />
          </>
        )}
      </div>
      <ErrorBanner message={resource.error || error} />
      {!resource.data && !resource.error ? (
        <Loading />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              {table.getHeaderGroups().map((group) => (
                <tr key={group.id}>
                  {group.headers.map((header) => (
                    <th key={header.id}>
                      {header.column.getCanSort() ? (
                        <button
                          className="flex items-center gap-2"
                          onClick={header.column.getToggleSortingHandler()}
                        >
                          {flexRender(
                            header.column.columnDef.header,
                            header.getContext(),
                          )}
                          <ArrowDownUp size={11} />
                          {header.column.getIsSorted() === "asc"
                            ? "↑"
                            : header.column.getIsSorted() === "desc"
                              ? "↓"
                              : ""}
                        </button>
                      ) : (
                        flexRender(
                          header.column.columnDef.header,
                          header.getContext(),
                        )
                      )}
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody>
              {table.getRowModel().rows.map((row) => (
                <tr key={row.original.stock_code}>
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id}>
                      {flexRender(
                        cell.column.columnDef.cell,
                        cell.getContext(),
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {resource.data?.total === 0 && (
            <p className="p-8 text-center text-sm text-muted-foreground">
              此条件下无结果，可调整参数或查看数据不足的股票。
            </p>
          )}
        </div>
      )}
      <div className="flex flex-wrap items-center justify-between gap-3 p-4 text-xs text-muted-foreground">
        <span>
          共 {resource.data?.total ?? 0} 条 · 第 {page} 页
        </span>
        <div className="flex items-center gap-2">
          <select
            aria-label="每页数量"
            className="!min-h-8 !w-24 !py-1"
            value={pageSize}
            onChange={(e) => {
              setPageSize(Number(e.target.value));
              setPage(1);
            }}
          >
            {[10, 20, 50, 100].map((n) => (
              <option value={n} key={n}>
                {n} 条/页
              </option>
            ))}
          </select>
          <Button
            variant="outline"
            size="sm"
            disabled={page === 1}
            onClick={() => setPage((p) => p - 1)}
          >
            上一页
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!resource.data || page * pageSize >= resource.data.total}
            onClick={() => setPage((p) => p + 1)}
          >
            下一页
          </Button>
        </div>
      </div>
    </Card>
  );
}
