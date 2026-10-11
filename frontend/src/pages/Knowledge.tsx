import { useEffect } from "react";
import { BookOpen, ExternalLink } from "lucide-react";
import { Link, useLocation } from "react-router-dom";
import { PageTitle } from "../components/common";
import { Card } from "../components/ui/card";
import { parameterLabels, strategies, warmupDays } from "../comparison";
import { strategyArticles } from "../knowledge";

const chapterIds = new Set<string>([
  "knowledge-top",
  "common-rules",
  ...strategies.map((s) => s.id),
]);

export default function Knowledge() {
  const { hash, key } = useLocation();
  useEffect(() => {
    const id = hash.slice(1);
    if (!chapterIds.has(id)) return;
    const heading = document.getElementById(id);
    heading?.scrollIntoView({ block: "start" });
    heading?.focus({ preventScroll: true });
  }, [hash, key]);

  return (
    <>
      <div id="knowledge-top" tabIndex={-1} className="scroll-mt-6">
        <PageTitle
          title="知识库"
          subtitle="读懂六种回测策略：基本思路、本站规则与可核对的信号示例。"
        />
      </div>
      <div className="grid items-start gap-5 lg:grid-cols-[210px_minmax(0,1fr)] xl:gap-8">
        <nav
          aria-label="策略目录"
          className="rounded-xl border border-border bg-white p-4 lg:sticky lg:top-6"
        >
          <p className="mb-3 flex items-center gap-2 text-sm font-semibold">
            <BookOpen size={16} aria-hidden="true" />
            策略目录
          </p>
          <div className="grid grid-cols-2 gap-1 lg:grid-cols-1">
            <Link
              to="#common-rules"
              className="col-span-2 rounded-lg px-3 py-2 text-sm text-primary hover:bg-muted focus-visible:outline-2 lg:col-span-1"
            >
              共同回测口径
            </Link>
            {strategies.map((strategy) => (
              <Link
                key={strategy.id}
                to={`#${strategy.id}`}
                aria-current={
                  hash === `#${strategy.id}` ? "location" : undefined
                }
                className={`rounded-lg px-3 py-2 text-sm hover:bg-muted focus-visible:outline-2 ${hash === `#${strategy.id}` ? "bg-muted font-medium text-primary" : "text-muted-foreground"}`}
              >
                {strategy.label}
              </Link>
            ))}
          </div>
          <p className="mt-4 border-t border-border pt-3 text-xs leading-5 text-muted-foreground">
            正文按本站现行实现说明。延伸阅读介绍通用概念，具体信号可能与本站不同。
          </p>
        </nav>
        <div className="min-w-0 space-y-5">
          <Card className="p-5 md:p-7" aria-labelledby="common-rules">
            <h2
              id="common-rules"
              tabIndex={-1}
              className="scroll-mt-6 text-lg font-semibold"
            >
              共同回测口径
            </h2>
            <div className="mt-4 space-y-3 text-sm leading-7 text-muted-foreground">
              <p>
                每次研究固定股票、起止交易日和数据版本。指标按信号日锚定复权，只使用截至当天的信息；成交和估值使用未复权价格。预热只计算指标，不成交。单股策略对比中，各策略独立使用同一笔初始资金与相同费用，不分摊资金。
              </p>
              <p>
                收盘产生信号，下一交易日开盘尝试成交。模拟遵守
                T+1；停牌、零成交量或开盘涨停阻止买入，开盘跌停阻止卖出，市场受阻时延期重试。信号不是成交保证，重复同向信号不加仓。期末不强制清仓，最后一天的信号不强行成交。
              </p>
              <p>
                新研究仅涵盖固定数据版本中的存续沪深 A
                股，存在幸存者偏差；供应商历史修订的完整时点快照尚未重建。缺失行情、预热不足或未知公司行动会导致结果不完整，完整收益指标返回未知，不能把已有部分曲线当作完整收益。
              </p>
              <details className="rounded-lg border border-border px-4 py-3">
                <summary className="cursor-pointer font-medium text-foreground">
                  费用、现金分红与模拟边界
                </summary>
                <div className="mt-3 space-y-3">
                  <p>
                    费用全区间使用提交时的固定参数：默认双向佣金万三、最低 5
                    元，双向过户费十万分之一，卖出印花税万分之五；这些是模拟假设，不重建历史实际费率。主板/创业板买入按
                    100 股整数倍，科创板至少 200 股、之后按 1
                    股递增；资金不足时保留余款。
                  </p>
                  <p>
                    只处理可确认公开且已实施、派息的现金分红。登记日持仓确认权益，派息到账；口径或完整覆盖不确定时结果未知。回测按持有期计算已确定的分红补税，未卖出持仓不预扣未来补税；期末资产不等于全部清仓后的税后收益。
                  </p>
                  <p>
                    日线无法确认真实排队位置与开盘成交量。本站提供个人研究模拟，不自动交易，不进行参数寻优；默认参数不表示最优参数，历史回测不保证未来表现。
                  </p>
                </div>
              </details>
            </div>
          </Card>
          {strategies.map((strategy, index) => {
            const article = strategyArticles[strategy.id];
            const parameters = Object.entries(strategy.defaults);
            return (
              <Card
                key={strategy.id}
                className="min-w-0 p-5 md:p-7"
                aria-labelledby={strategy.id}
              >
                <div className="mb-4 flex flex-wrap items-center gap-3">
                  <span
                    aria-hidden="true"
                    className="flex h-8 w-8 items-center justify-center rounded-lg bg-muted text-xs font-semibold text-primary"
                  >
                    0{index + 1}
                  </span>
                  <h2
                    id={strategy.id}
                    tabIndex={-1}
                    className="scroll-mt-6 text-xl font-semibold"
                  >
                    {strategy.label}
                  </h2>
                  <span className="badge">{article.category}</span>
                </div>
                <p className="text-sm leading-7 text-muted-foreground">
                  {article.idea}
                </p>
                <div className="mt-5 rounded-lg bg-muted/60 p-4 text-sm leading-7">
                  <h3 className="font-semibold">指标与默认参数</h3>
                  <p className="mt-2 break-words text-muted-foreground">
                    {article.calculation}
                  </p>
                  {parameters.length > 0 ? (
                    <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-2">
                      {parameters.map(([key, value]) => (
                        <div key={key} className="flex gap-2">
                          <dt className="text-muted-foreground">
                            {parameterLabels[key]}
                          </dt>
                          <dd className="font-medium tabular-nums">{value}</dd>
                        </div>
                      ))}
                    </dl>
                  ) : (
                    <p className="mt-3">无技术指标参数</p>
                  )}
                  <p className="mt-2 text-xs text-muted-foreground">
                    预热：开始日前{" "}
                    {warmupDays({ id: strategy.id, ...strategy.defaults })}{" "}
                    个交易日。周期与窗口按交易日计算；调整参数会改变预热需求。
                  </p>
                </div>
                <h3 className="mb-2 mt-5 text-sm font-semibold">本站规则</h3>
                <ul className="list-disc space-y-2 pl-5 text-sm leading-7 text-muted-foreground">
                  {article.rules.map((rule) => (
                    <li key={rule}>{rule}</li>
                  ))}
                </ul>
                <h3 className="mb-2 mt-5 text-sm font-semibold">信号示例</h3>
                <p className="mb-3 text-xs text-muted-foreground">
                  教学示例 · 指标数值为人工设定，不代表真实行情
                </p>
                <div className="space-y-2">
                  {article.examples.map((example) => (
                    <dl
                      key={example.signal}
                      className="grid gap-3 rounded-lg border border-border p-4 text-sm leading-6 md:grid-cols-3"
                    >
                      <div>
                        <dt className="mb-1 text-xs text-muted-foreground">
                          前一状态
                        </dt>
                        <dd>{example.before}</dd>
                      </div>
                      <div>
                        <dt className="mb-1 text-xs text-muted-foreground">
                          当日收盘
                        </dt>
                        <dd>{example.after}</dd>
                      </div>
                      <div>
                        <dt className="mb-1 text-xs text-muted-foreground">
                          信号判断
                        </dt>
                        <dd className="font-medium text-primary">
                          {example.signal}
                        </dd>
                      </div>
                    </dl>
                  ))}
                </div>
                <p className="mt-3 text-xs leading-6 text-muted-foreground">
                  上述买卖信号均在收盘确认，下一交易日开盘尝试成交；受持仓、资金与市场约束，实际成交可能延期或不发生。
                </p>
                <h3 className="mb-2 mt-5 text-sm font-semibold">局限</h3>
                <p className="text-sm leading-7 text-muted-foreground">
                  {article.limitation}
                </p>
                <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4 text-xs">
                  <a
                    href={article.reference.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex min-w-0 items-center gap-2 break-words text-primary underline underline-offset-4"
                  >
                    <span>
                      延伸阅读 · {article.reference.publisher} ·{" "}
                      {article.reference.title}（英文，新窗口）
                    </span>
                    <ExternalLink
                      size={14}
                      className="shrink-0"
                      aria-hidden="true"
                    />
                  </a>
                  <Link
                    to="#knowledge-top"
                    className="shrink-0 text-muted-foreground hover:underline"
                  >
                    返回顶部
                  </Link>
                </div>
              </Card>
            );
          })}
        </div>
      </div>
    </>
  );
}
