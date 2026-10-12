import { useEffect, useRef, useState } from "react";
import { api, useResource } from "../api";
import type { CompanyProfile, CompanySection } from "../types";
import { Button } from "./ui/button";
import { Card } from "./ui/card";
import { ErrorBanner } from "./common";
import { formatCompactNumber } from "../format";

type Row = Record<string, string | number | null>;
type Field = readonly [string, string, string?];
const indicatorFields: Field[] = [
  ["eps", "每股收益", "元/股"],
  ["bps", "每股净资产", "元/股"],
  ["roe", "净资产收益率", "%"],
  ["grossprofit_margin", "毛利率", "%"],
  ["netprofit_margin", "净利率", "%"],
  ["debt_to_assets", "资产负债率", "%"],
  ["or_yoy", "营业收入同比", "%"],
  ["netprofit_yoy", "归母净利润同比", "%"],
  ["dt_netprofit_yoy", "扣非净利润同比", "%"],
];
const incomeFields: Field[] = [
  ["total_revenue", "营业总收入", "元"],
  ["revenue", "营业收入", "元"],
  ["operate_profit", "营业利润", "元"],
  ["n_income_attr_p", "归母净利润", "元"],
];
const balanceFields: Field[] = [
  ["total_assets", "总资产", "元"],
  ["total_liab", "总负债", "元"],
  ["total_hldr_eqy_exc_min_int", "归母权益", "元"],
];
const cashFields: Field[] = [
  ["n_cashflow_act", "经营现金流净额", "元"],
  ["n_cashflow_inv_act", "投资现金流净额", "元"],
  ["n_cash_flows_fnc_act", "筹资现金流净额", "元"],
  ["c_cash_equ_end_period", "期末现金及等价物", "元"],
];
const companyFields: Field[] = [
  ["com_name", "公司全称"],
  ["com_id", "统一社会信用代码"],
  ["setup_date", "成立日期"],
  ["reg_capital", "注册资本", "万元"],
  ["province", "省份"],
  ["city", "城市"],
  ["chairman", "法人代表"],
  ["manager", "总经理"],
  ["secretary", "董秘"],
  ["employees", "员工人数", "人"],
  ["website", "公司网站"],
  ["office", "办公地址"],
];
function value(
  row: Row | undefined,
  [key, , unit]: Field,
  showUnit = true,
  compact = true,
) {
  const raw = row?.[key];
  if (raw == null || raw === "") return "—";
  if (typeof raw !== "number") return String(raw);
  if (!Number.isFinite(raw)) return "—";
  // Local daily_basic is already in yuan; stock_company capital remains in 10k yuan.
  if (unit === "元" || unit === "万元") {
    const yuan = unit === "万元" ? raw * 1e4 : raw;
    return compact
      ? formatCompactNumber(yuan, "元")
      : `${yuan.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}元`;
  }
  if (unit === "人")
    return compact
      ? formatCompactNumber(raw, "人")
      : `${raw.toLocaleString("zh-CN")}人`;
  return `${raw.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}${showUnit ? (unit ?? "") : ""}`;
}
function State({ section }: { section?: CompanySection }) {
  if (!section)
    return (
      <p className="py-4 text-sm text-muted-foreground">等待加载企业资料。</p>
    );
  if (section.status === "empty")
    return (
      <p className="py-4 text-sm text-muted-foreground">暂无可用披露数据。</p>
    );
  return section.status !== "ready" ? (
    <p role="status" className="py-4 text-sm text-amber-800">
      {section.message ?? "资料暂时不可用，可稍后刷新。"}
    </p>
  ) : null;
}
function Source({ section }: { section?: CompanySection }) {
  return section ? (
    <p className="mt-3 text-xs text-muted-foreground">
      来源：{section.source} · 获取时间：
      {new Date(section.fetched_at).toLocaleString("zh-CN")}
    </p>
  ) : null;
}
function Metrics({ row, fields }: { row?: Row; fields: Field[] }) {
  return (
    <dl className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {fields.map((field) => (
        <div className="min-w-0" key={field[0]}>
          <dt className="text-xs text-muted-foreground">{field[1]}</dt>
          <dd
            className="mt-2 break-words whitespace-normal text-sm"
            title={value(row, field, true, false)}
          >
            {value(row, field)}
          </dd>
        </div>
      ))}
    </dl>
  );
}
function FinancialTable({
  title,
  section,
  fields,
  cumulative = true,
}: {
  title: string;
  section?: CompanySection;
  fields: Field[];
  cumulative?: boolean;
}) {
  return (
    <Card className="mt-4 min-w-0 p-5">
      <h3 className="font-semibold">{title}</h3>
      <p className="mt-2 text-xs text-muted-foreground">
        最近12个季度报告期 · 合并报表 ·{" "}
        {cumulative ? "年初至报告期末累计口径，非单季度" : "报告期末余额"}
      </p>
      <State section={section} />
      {section?.status === "ready" && (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr>
                <th>报告期</th>
                <th>公告日</th>
                {fields.map(([key, label, unit]) => (
                  <th key={key}>
                    {label}
                    {unit && unit !== "元" && `（${unit}）`}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {section.items.map((row, i) => (
                <tr key={i}>
                  <td>{row.end_date ?? "—"}</td>
                  <td>{row.f_ann_date ?? row.ann_date ?? "—"}</td>
                  {fields.map((field) => (
                    <td key={field[0]} title={value(row, field, true, false)}>
                      {value(row, field, false)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Source section={section} />
    </Card>
  );
}
export default function CompanyDetails({ code }: { code: string }) {
  const resource = useResource<CompanyProfile>(
    `/instruments/${encodeURIComponent(code)}/company`,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [businessType, setBusinessType] = useState<"products" | "regions">(
    "products",
  );
  const attempted = useRef("");
  async function refresh(force = false) {
    setBusy(true);
    setError("");
    try {
      await api(
        `/instruments/${encodeURIComponent(code)}/company/refresh${force ? "?force=true" : ""}`,
        { method: "POST" },
      );
      resource.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "企业资料加载失败");
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    if (
      resource.data?.stock_code === code &&
      resource.data.stale &&
      attempted.current !== code
    ) {
      attempted.current = code;
      void refresh();
    }
  }, [code, resource.data]);
  useEffect(() => {
    if (
      !resource.data?.job ||
      !["queued", "running", "cancel_requested"].includes(
        resource.data.job.status,
      )
    )
      return;
    const timer = setTimeout(resource.reload, 2000);
    return () => clearTimeout(timer);
  }, [resource.data, resource.reload]);
  const profile = resource.data;
  const sections = profile?.sections ?? {};
  const company = sections.company?.items[0];
  const indicators = sections.indicators?.items[0];
  const business = sections[businessType];
  const active =
    profile?.job &&
    ["queued", "running", "cancel_requested"].includes(profile.job.status);
  return (
    <section className="mt-6 min-w-0" aria-label="企业情况">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-semibold">
            最新企业资料 · {profile?.as_of_date ?? "待获取"}
          </h2>
          <p className="mt-2 text-xs text-muted-foreground">
            最新披露及当前企业信息；与上方历史筛选行情分别展示。
            大数超过10亿用亿（1位小数），超过100万用万（整数）；悬停数值查看完整金额。
            {profile?.stale && " 缓存待更新。"}
          </p>
        </div>
        <Button
          variant="outline"
          disabled={busy || !!active}
          onClick={() => void refresh(true)}
        >
          {busy || active ? "资料加载中" : "刷新企业资料"}
        </Button>
      </div>
      {active && (
        <p role="status" className="mt-3 text-sm text-muted-foreground">
          正在加载企业资料 · {Math.round((profile?.job?.progress ?? 0) * 100)}%
        </p>
      )}
      {profile?.fixture_mode && (
        <p role="status" className="mt-3 text-sm text-amber-800">
          fixture 模式：企业资料来自合成测试数据。
        </p>
      )}
      <ErrorBanner message={error || resource.error} />
      <Card className="mt-4 min-w-0 p-5">
        <h3 className="font-semibold">企业概览</h3>
        <State section={sections.company} />
        {sections.company?.status === "ready" && (
          <>
            <Metrics row={company} fields={companyFields} />
            {[
              ["introduction", "公司介绍"],
              ["main_business", "主营业务"],
              ["business_scope", "经营范围"],
            ].map(([key, label]) => (
              <div key={key} className="mt-5">
                <h4 className="text-sm font-medium">{label}</h4>
                <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-muted-foreground">
                  {company?.[key] ?? "—"}
                </p>
              </div>
            ))}
          </>
        )}
        <Source section={sections.company} />
      </Card>
      <Card className="mt-4 min-w-0 p-5">
        <h3 className="font-semibold">基本面 · 最新披露</h3>
        <p className="mt-2 text-xs text-muted-foreground">
          报告期：{indicators?.end_date ?? "—"} · 公告日：
          {indicators?.ann_date ?? "—"}
        </p>
        <State section={sections.indicators} />
        {sections.indicators?.status === "ready" && (
          <Metrics row={indicators} fields={indicatorFields} />
        )}
        <Source section={sections.indicators} />
        {profile?.valuation && (
          <div className="mt-5 border-t border-border pt-4">
            <p className="text-xs text-muted-foreground">
              估值行情日：{profile.valuation.trade_date} · 来源：
              {profile.valuation.source ?? "未知"} · 本地已同步行情
            </p>
            <Metrics
              row={profile.valuation}
              fields={[
                ["pe", "PE"],
                ["pe_ttm", "PE TTM"],
                ["pb", "PB"],
                ["total_mv", "总市值", "元"],
                ["circ_mv", "流通市值", "元"],
              ]}
            />
          </div>
        )}
      </Card>
      <Card className="mt-4 min-w-0 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h3 className="font-semibold">业务构成</h3>
          <select
            aria-label="业务构成维度"
            className="!w-32"
            value={businessType}
            onChange={(e) =>
              setBusinessType(e.target.value as typeof businessType)
            }
          >
            <option value="products">按产品</option>
            <option value="regions">按地区</option>
          </select>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          报告期：{business?.items[0]?.end_date ?? "—"} ·
          最新可用披露，按原币种展示
        </p>
        <State section={business} />
        {business?.status === "ready" && (
          <div className="mt-4 overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr>
                  <th>业务项目</th>
                  <th>币种</th>
                  <th>收入</th>
                  <th>成本</th>
                  <th>利润</th>
                </tr>
              </thead>
              <tbody>
                {business.items.map((row, i) => (
                  <tr key={i}>
                    <td>{row.bz_item}</td>
                    <td>{row.curr_type ?? "未知"}</td>
                    {["bz_sales", "bz_cost", "bz_profit"].map((key) => (
                      <td key={key} title={value(row, [key, ""], false, false)}>
                        {typeof row[key] === "number"
                          ? formatCompactNumber(
                              row[key],
                              row.curr_type === "CNY" ? "元" : "",
                            )
                          : value(row, [key, ""])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <Source section={business} />
      </Card>
      <FinancialTable
        title="经营情况 · 利润表"
        section={sections.income}
        fields={incomeFields}
      />
      <FinancialTable
        title="经营情况 · 资产负债表"
        section={sections.balance}
        fields={balanceFields}
        cumulative={false}
      />
      <FinancialTable
        title="现金流"
        section={sections.cashflow}
        fields={cashFields}
      />
    </section>
  );
}
