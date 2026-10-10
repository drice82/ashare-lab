import { DeletableTasks } from "../components/TaskHistory";
import { ArrowRight, Database, Filter, Layers, RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";
import { useResource } from "../api";
import {
  ErrorBanner,
  Loading,
  PageTitle,
} from "../components/common";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { statusLabels } from "../format";
import type { MarketStatus } from "../types";

export default function Dashboard() {
  const { data, error, reload } = useResource<MarketStatus>("/market/status", 15000);
  return (
    <>
      <PageTitle
        title="研究概览"
        subtitle="从可靠的数据开始，发现值得进一步研究的投资线索。"
        action={
          <Button asChild>
            <Link to="/screening">
              <Filter size={15} />
              开始股票筛选
            </Link>
          </Button>
        }
      />
      <ErrorBanner message={error} />
      {!data && !error ? (
        <Loading />
      ) : (
        data && (
          <>
            {data.fixture_mode && (
              <div className="mb-5 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                固定测试数据 · 此环境用于验证计算流程，不代表真实市场行情。
              </div>
            )}
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              {[
                {
                  label: "最新行情日期",
                  value: data.latest_market_date ?? "尚未同步",
                  icon: Database,
                  note: "日线 · 收盘后研究",
                },
                {
                  label: "有效股票池",
                  value: `${data.stock_count.toLocaleString()} 只`,
                  icon: Layers,
                  note: "沪深 A 股",
                },
                {
                  label: "最近同步状态",
                  value:
                    statusLabels[data.latest_sync_status ?? data.recent_sync[0]?.status] ?? "暂无任务",
                  icon: RefreshCw,
                  note: data.token_configured
                    ? "Tushare Token 已配置"
                    : "Tushare Token 未配置",
                },
                {
                  label: "最近筛选命中",
                  value: data.recent_screening[0]?.result
                    ? `${data.recent_screening[0].result.matched} 只`
                    : "—",
                  icon: Filter,
                  note:
                    data.recent_screening[0]?.payload.as_of_date ??
                    "开始第一次筛选",
                },
              ].map((item) => (
                <Card className="p-5" key={item.label}>
                  <div className="flex justify-between text-sm text-muted-foreground">
                    {item.label}
                    <item.icon size={17} />
                  </div>
                  <p className="my-4 text-2xl font-semibold tabular-nums">
                    {item.value}
                  </p>
                  <p className="text-xs text-muted-foreground">{item.note}</p>
                </Card>
              ))}
            </div>
            <Card className="my-6 flex flex-wrap items-center justify-between gap-5 bg-[#eaf0e7] p-6">
              <div>
                <p className="eyebrow">RESEARCH, WITH CONTEXT</p>
                <h2 className="my-2 text-lg font-semibold">
                  让每一个筛选结果都有依据
                </h2>
                <p className="max-w-2xl text-sm text-muted-foreground">
                  历史日期、固定数据版本与逐条件数值，让你的研究可以复核。分红使用已实施现金事件，数据缺口会明确标记。
                </p>
              </div>
              <Button asChild variant="outline">
                <Link to="/data">
                  查看数据覆盖
                  <ArrowRight size={15} />
                </Link>
              </Button>
            </Card>
            <Card>
              <div className="flex items-center justify-between border-b border-border p-5">
                <h2 className="font-semibold">最近筛选任务</h2>
                <Link className="text-xs text-primary" to="/screening">
                  进入筛选工作台 →
                </Link>
              </div>
              <DeletableTasks jobs={data.recent_screening} onDeleted={() => reload()} />
            </Card>
            <p className="mt-5 text-xs text-muted-foreground">
              历史可见性限制：{data.pit_limitation}。交易日缺口：
              {data.missing_trade_dates.length} 天。
            </p>
          </>
        )
      )}
    </>
  );
}
