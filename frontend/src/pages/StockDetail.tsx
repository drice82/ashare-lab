import { Link, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { useResource } from "../api";
import { formatFactor, statusLabels, factorClass } from "../format";
import { ErrorBanner, Loading, PageTitle } from "../components/common";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import PriceChart from "../components/PriceChart";
import type { BarsResponse, Factor, Results } from "../types";

const flags: Record<string, string> = {
  conservative_pit: "按公告日期保守重建",
  historical_provider_revisions_not_reconstructed: "供应商历史修订快照未重建",
  display_name_current_snapshot: "展示名称来自当前快照",
  historical_st_unknown: "历史 ST 状态未知",
  dividend_history_incomplete: "分红历史不完整",
  dividend_visibility_unknown: "分红可见时间无法确认",
  unsupported_share_basis: "股本变动后每股口径无法确认",
  missing_current_bar: "筛选日无有效行情",
  insufficient_20d_bars: "20日窗口行情不足",
  missing_adjustment_or_window: "复权因子或窗口不完整",
  suspension_dataset_unavailable: "停复牌数据不可用",
};
const operators: Record<string, string> = {
  gt: ">",
  gte: "≥",
  lt: "<",
  lte: "≤",
  eq: "=",
};
export default function StockDetail() {
  const { code = "" } = useParams();
  const [query] = useSearchParams();
  const runId = query.get("run");
  const bars = useResource<BarsResponse>(
    `/market/bars?${new URLSearchParams({ stock_code: code, ...(runId ? { run_id: runId } : {}) })}`,
  );
  const results = useResource<Results>(
    runId
      ? `/screening/runs/${runId}/results?match_status=all&search=${encodeURIComponent(code)}`
      : null,
  );
  const registry = useResource<{ factors: Factor[] }>("/screening/rules");
  const row = results.data?.items.find((item) => item.stock_code === code);
  const names = new Map(registry.data?.factors.map((f) => [f.id, f.name]));
  return (
    <>
      <PageTitle
        title={row ? `${row.stock_name} · ${code}` : code}
        subtitle={`股票详情 · ${row?.as_of_date ?? bars.data?.anchor_date ?? ""}`}
        action={
          <Button asChild variant="outline">
            <Link to={runId ? `/screening?run=${runId}` : "/screening"}>
              <ArrowLeft size={15} />
              返回筛选
            </Link>
          </Button>
        }
      />
      <ErrorBanner message={bars.error || results.error} />
      {bars.data?.instrument && (
        <div className="mb-5 grid gap-4 sm:grid-cols-4">
          {[
            ["上市日期", bars.data.instrument.list_date],
            ["交易所", bars.data.instrument.exchange],
            ["行业（当前快照）", bars.data.instrument.industry ?? "—"],
            ["原始收盘价", formatFactor("close", row?.factor_values.close)],
          ].map(([label, value]) => (
            <Card className="p-4" key={label}>
              <p className="text-xs text-muted-foreground">{label}</p>
              <p className="mt-3 font-medium">{value}</p>
            </Card>
          ))}
        </div>
      )}
      <Card className="overflow-hidden p-5">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
          <h2 className="font-semibold">日 K 线与成交量</h2>
          <div className="flex gap-4 text-xs">
            <span className="text-[#bd933d]">● MA20</span>
            <span className="text-[#788db0]">● MA60</span>
            <span className="text-muted-foreground">
              截至 {bars.data?.anchor_date ?? "—"} 复权 · 元 / 股
            </span>
          </div>
        </div>
        {!bars.data && !bars.error ? (
          <Loading />
        ) : bars.data?.items.length ? (
          <PriceChart bars={bars.data.items} />
        ) : (
          <p className="py-12 text-center text-sm text-muted-foreground">
            没有可绘制的有效行情。
          </p>
        )}
        <p className="mt-2 text-[11px] text-muted-foreground">
          图表引擎：
          <a
            href="https://www.tradingview.com/"
            target="_blank"
            rel="noreferrer"
            className="underline"
          >
            TradingView Lightweight Charts™
          </a>{" "}
          · 缺失窗口不填充均线。
        </p>
      </Card>
      {row && (
        <Card className="mt-6 p-5">
          <h2 className="mb-4 font-semibold">
            规则 {row.rule_id} · {statusLabels[row.match_status]} · 具体触发数值
          </h2>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr>
                  <th>因子</th>
                  <th>实际值</th>
                  <th>条件</th>
                  <th>判断</th>
                </tr>
              </thead>
              <tbody>
                {row.condition_results.map((condition, i) => (
                  <tr key={i}>
                    <td>{names.get(condition.factor) ?? condition.factor}</td>
                    <td
                      className={factorClass(
                        condition.factor,
                        condition.actual,
                      )}
                    >
                      {formatFactor(condition.factor, condition.actual)}
                    </td>
                    <td>
                      {operators[condition.operator]}{" "}
                      {formatFactor(condition.factor, condition.threshold)}
                    </td>
                    <td>
                      <span className="badge">
                        {condition.matched === null
                          ? "数据不足"
                          : condition.matched
                            ? "满足"
                            : "不满足"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <h3 className="mt-5 text-sm font-medium">数据质量与历史可见性</h3>
          <div className="mt-3 flex flex-wrap gap-2">
            {row.data_quality_flags.map((flag) => (
              <span className="badge" key={flag}>
                {flags[flag] ?? flag}
              </span>
            ))}
          </div>
        </Card>
      )}
    </>
  );
}
