import { render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import CompanyDetails from "./CompanyDetails";

it("最新资料独立标记，财务百分比不重复乘100，权限错误分区显示", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        stock_code: "000001.SZ",
        stale: false,
        fixture_mode: true,
        as_of_date: "2026-10-11",
        sections: {
          company: {
            status: "ready",
            source: "fixture",
            fetched_at: "2026-10-11T12:00:00Z",
            items: [
              {
                com_name: "测试企业",
                introduction: "公司介绍",
                reg_capital: 100,
              },
            ],
          },
          indicators: {
            status: "ready",
            source: "fixture",
            fetched_at: "2026-10-11T12:00:00Z",
            items: [
              { end_date: "2026-06-30", ann_date: "2026-08-01", roe: 12.5 },
            ],
          },
          cashflow: {
            status: "permission_denied",
            source: "fixture",
            fetched_at: "2026-10-11T12:00:00Z",
            items: [],
            message: "接口权限不足",
          },
        },
      }),
    ),
  );
  render(<CompanyDetails code="000001.SZ" />);
  await screen.findByText("测试企业");
  expect(screen.getByText("12.50%")).toBeInTheDocument();
  expect(screen.getByText(/最新企业资料/)).toBeInTheDocument();
  expect(screen.getByText("接口权限不足")).toBeInTheDocument();
  expect(screen.getByText(/fixture 模式/)).toBeInTheDocument();
});

it("已标准化市值按元展示，注册资本从万元换算，报表与外币业务逐项标单位", async () => {
  const section = (items: Record<string, string | number | null>[]) => ({
    status: "ready", source: "fixture", fetched_at: "2026-10-11T12:00:00Z", items,
  });
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({
    stock_code: "000001.SZ", stale: false, fixture_mode: true,
    valuation: { trade_date: "2026-10-09", total_mv: 12_345_678_900, circ_mv: 6_789_012_300, pe: 12.34 },
    sections: {
      company: section([{ reg_capital: 234_567.89, employees: 2345 }]),
      indicators: section([{ roe: 12.5, eps: 1.23 }]),
      products: section([{ bz_item: "外币业务", curr_type: "USD", bz_sales: 12_345_678, bz_cost: 500, bz_profit: null }]),
      income: section([{ total_revenue: 2_345_678_900, revenue: 2_345_678, operate_profit: 500, n_income_attr_p: null }]),
      balance: section([{ total_assets: 10_000_000_000 }]),
      cashflow: section([{ n_cashflow_act: -1_234_567_890, n_cashflow_inv_act: -2_345_678 }]),
    },
  })));
  render(<CompanyDetails code="000001.SZ" />);
  await screen.findByText("123.5亿元");
  expect(screen.getByText("67.9亿元")).toBeInTheDocument();
  expect(screen.getAllByText("23.5亿元")).toHaveLength(2);
  expect(screen.getByText("123.5亿元")).toHaveAttribute("title", "12,345,678,900元");
  expect(screen.getByText("2,345人")).toBeInTheDocument();
  expect(screen.getByText("1.23元/股")).toBeInTheDocument();
  expect(screen.getByText("12.50%")).toBeInTheDocument();
  expect(screen.getByText("-12.3亿元")).toBeInTheDocument();
  expect(screen.getByText("-235万元")).toBeInTheDocument();
  const business = screen.getByText("外币业务").closest("tr")!;
  expect(within(business).getByText("1,235万")).toBeInTheDocument();
  expect(within(business).getByText("USD")).toBeInTheDocument();
  expect(within(business).queryByText(/元/)).not.toBeInTheDocument();
  expect(screen.queryByRole("columnheader", { name: /亿元|收入（元）/ })).not.toBeInTheDocument();
});
