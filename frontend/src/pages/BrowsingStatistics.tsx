import { useEffect, useState } from "react";
import { useResource } from "../api";
import { ErrorBanner, Loading } from "../components/common";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";

type Counts = { pageviews: number; visitors: number };
type DailyCounts = Counts & { date: string };
type Statistics = {
  start_date: string;
  end_date: string;
  today: string;
  earliest_date: string;
  timezone: string;
  retention_days: number;
  totals: Counts;
  daily: DailyCounts[];
  pages: (Counts & { page: string; name: string; share: number })[];
};

function shiftDate(day: string, offset: number) {
  const date = new Date(`${day}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + offset);
  return date.toISOString().slice(0, 10);
}

function Trend({ daily }: { daily: DailyCounts[] }) {
  const maximum = Math.max(1, ...daily.map((day) => day.pageviews));
  const x = (index: number) =>
    daily.length > 1 ? 45 + (index * 510) / (daily.length - 1) : 300;
  const y = (value: number) => 175 - (value / maximum) * 140;
  return (
    <>
      <div className="mb-3 flex flex-wrap gap-5 text-xs">
        <span className="text-[#256b4a]">● 浏览次数</span>
        <span className="text-[#3869ae]">● 独立访客</span>
      </div>
      <svg
        viewBox="0 0 600 215"
        className="w-full"
        role="img"
        aria-label="每日浏览次数与独立访客趋势"
      >
        <title>每日浏览次数与独立访客趋势，详细数值见下方每日数据</title>
        {[0, maximum / 2, maximum].map((value) => (
          <g key={value}>
            <line
              x1="45"
              x2="555"
              y1={y(value)}
              y2={y(value)}
              stroke="#e2e8df"
            />
            <text
              x="35"
              y={y(value) + 4}
              textAnchor="end"
              fontSize="11"
              fill="#64748b"
            >
              {Number.isInteger(value) ? value : value.toFixed(1)}
            </text>
          </g>
        ))}
        {(["pageviews", "visitors"] as const).map((field, series) => (
          <g key={field}>
            <polyline
              points={daily
                .map((day, index) => `${x(index)},${y(day[field])}`)
                .join(" ")}
              fill="none"
              stroke={series === 0 ? "#256b4a" : "#3869ae"}
              strokeWidth="2"
            />
            {daily.map((day, index) => (
              <circle
                key={day.date}
                cx={x(index)}
                cy={y(day[field])}
                r="3"
                fill={series === 0 ? "#256b4a" : "#3869ae"}
              >
                <title>
                  {day.date}：浏览 {day.pageviews} 次，访客 {day.visitors} 人
                </title>
              </circle>
            ))}
          </g>
        ))}
        <text x="45" y="203" fontSize="11" fill="#64748b">
          {daily[0]?.date}
        </text>
        <text x="555" y="203" textAnchor="end" fontSize="11" fill="#64748b">
          {daily.at(-1)?.date}
        </text>
      </svg>
      <details className="mt-2 text-sm">
        <summary className="cursor-pointer text-muted-foreground">
          查看每日数据
        </summary>
        <div className="mt-3 max-h-72 overflow-auto">
          <table className="w-full">
            <thead>
              <tr>
                <th>日期</th>
                <th>浏览次数</th>
                <th>独立访客</th>
              </tr>
            </thead>
            <tbody>
              {daily.map((day) => (
                <tr key={day.date}>
                  <td>{day.date}</td>
                  <td>{day.pageviews.toLocaleString()}</td>
                  <td>{day.visitors.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </>
  );
}

export default function BrowsingStatistics({
  password,
  active,
  fixtureMode = false,
}: {
  password: string;
  active: boolean;
  fixtureMode?: boolean;
}) {
  const [range, setRange] = useState<{ start: string; end: string }>();
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [bounds, setBounds] = useState<{ earliest: string; today: string }>();
  const [error, setError] = useState("");
  const suffix = range
    ? `?start_date=${range.start}&end_date=${range.end}`
    : "";
  const stats = useResource<Statistics>(
    active ? `/data/analytics${suffix}` : null,
    0,
    password,
  );
  useEffect(() => {
    if (!stats.data) return;
    setBounds({ earliest: stats.data.earliest_date, today: stats.data.today });
    setStart((current) => current || stats.data!.start_date);
    setEnd((current) => current || stats.data!.end_date);
  }, [stats.data]);
  function apply() {
    if (
      !bounds ||
      !start ||
      !end ||
      start < bounds.earliest ||
      end > bounds.today ||
      start > end
    ) {
      setError("请选择最近90天内的有效日期范围。");
      return;
    }
    setError("");
    if (range?.start === start && range.end === end) stats.reload();
    else setRange({ start, end });
  }
  function recent(days: number) {
    if (!bounds) return;
    const from = shiftDate(bounds.today, 1 - days);
    setStart(from);
    setEnd(bounds.today);
    setError("");
    if (range?.start === from && range.end === bounds.today) stats.reload();
    else setRange({ start: from, end: bounds.today });
  }
  return (
    <div className="space-y-5">
      <Card className="p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-semibold">浏览统计</h2>
          <Button variant="outline" size="sm" onClick={stats.reload}>
            刷新统计
          </Button>
        </div>
        <p className="mt-2 text-xs leading-5 text-muted-foreground">
          访客按浏览器识别，记录保留90天。刷新和页面切换计入浏览，页签与翻页不重复计数；后台访问不计入。
          {stats.data && (
            <> 日期按 {stats.data.timezone} 统计；区间访客数按整个区间去重。</>
          )}
        </p>
        {fixtureMode && (
          <p className="mt-3 text-sm text-amber-700">固定测试环境（fixture）</p>
        )}
        <div className="mt-4 flex flex-wrap gap-2">
          {[7, 30, 90].map((days) => (
            <Button
              key={days}
              variant="outline"
              size="sm"
              disabled={!bounds}
              onClick={() => recent(days)}
            >
              最近{days}天
            </Button>
          ))}
        </div>
        <form
          className="mt-4 grid items-end gap-3 sm:grid-cols-[1fr_1fr_auto]"
          onSubmit={(event) => {
            event.preventDefault();
            apply();
          }}
        >
          <label>
            开始日期
            <input
              aria-label="浏览开始日期"
              type="date"
              min={bounds?.earliest}
              max={bounds?.today}
              value={start}
              onChange={(event) => setStart(event.target.value)}
              required
            />
          </label>
          <label>
            结束日期
            <input
              aria-label="浏览结束日期"
              type="date"
              min={bounds?.earliest}
              max={bounds?.today}
              value={end}
              onChange={(event) => setEnd(event.target.value)}
              required
            />
          </label>
          <Button type="submit" disabled={!bounds}>
            查询统计
          </Button>
        </form>
      </Card>
      <ErrorBanner message={error || stats.error} />
      {active && !stats.data && !stats.error && <Loading />}
      {stats.data && (
        <>
          <p className="text-xs text-muted-foreground">
            统计范围：{stats.data.start_date} ~ {stats.data.end_date}
          </p>
          <div className="grid gap-4 sm:grid-cols-2">
            {[
              { label: "浏览次数", value: stats.data.totals.pageviews },
              { label: "独立访客数", value: stats.data.totals.visitors },
            ].map((item) => (
              <Card key={item.label} className="p-5">
                <p className="text-sm text-muted-foreground">{item.label}</p>
                <p className="mt-2 text-3xl font-semibold tabular-nums">
                  {item.value.toLocaleString()}
                </p>
              </Card>
            ))}
          </div>
          {stats.data.totals.pageviews === 0 && (
            <Card className="p-5 text-sm text-muted-foreground">
              所选日期范围暂无浏览记录。
            </Card>
          )}
          <Card className="p-5">
            <h3 className="mb-4 font-semibold">每日趋势</h3>
            <Trend daily={stats.data.daily} />
          </Card>
          <Card className="overflow-hidden">
            <h3 className="border-b border-border p-5 font-semibold">
              页面排行
            </h3>
            <div className="overflow-x-auto">
              <table className="w-full whitespace-nowrap">
                <thead>
                  <tr>
                    <th>页面</th>
                    <th>浏览次数</th>
                    <th>独立访客</th>
                    <th>浏览占比</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.data.pages.map((page) => (
                    <tr key={page.page}>
                      <td>{page.name}</td>
                      <td>{page.pageviews.toLocaleString()}</td>
                      <td>{page.visitors.toLocaleString()}</td>
                      <td>{(page.share * 100).toFixed(1)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
