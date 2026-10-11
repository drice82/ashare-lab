import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import Portfolio from "./Portfolio";

vi.mock("../components/PortfolioCharts", () => ({
  default: () => <div role="img" aria-label="组合收益曲线" />,
}));
vi.mock("../components/ComparisonCharts", () => ({
  TradeChart: () => <div role="img" aria-label="逐股买卖点" />,
}));
const versions = { daily: "old", trade_cal: "cal", stock_basic: "basic" };
const defaults = {
  initial_cash: "100000",
  allocation: "equal",
  rebalance: "monthly",
  commission_rate: "0.0003",
  minimum_commission: "5",
  transfer_rate: "0.00001",
  stamp_rate: "0.0005",
};
const summary = {
  complete: true,
  confirmed_through: "2024-06-28",
  contributed: 100000,
  cash: 1000,
  position_value: 99000,
  receivable: 0,
  tax_liability: 0,
  fees: 0,
  dividends: 0,
  dividend_tax_paid: 0,
  total_assets: 100000,
  profit: 0,
  return_rate: 0,
  annualized_return: 0,
  max_drawdown: 0,
  pending_orders: 0,
  buy_count: 2,
  sell_count: 0,
};
function mock(task = false) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, options?: RequestInit) => {
      if (url.endsWith("/portfolio/options"))
        return Response.json({
          defaults,
          fixture_mode: true,
          data_versions: versions,
          latest_market_date: "2024-06-28",
          calendar_start: "2023-01-01",
          trading_days: ["2023-06-28", "2024-06-03", "2024-06-28"],
        });
      if (url.includes("/instruments")) return Response.json({ items: [] });
      if (url.endsWith("/portfolio/run"))
        return Response.json({ id: "p1" }, { status: 202 });
      if (url.includes("/jobs?")) return Response.json([]);
      if (url.endsWith("/portfolio/runs/p1"))
        return Response.json({
          id: "p1",
          kind: "portfolio",
          status: "succeeded",
          progress: 1,
          payload: {
            ...defaults,
            members: [
              { stock_code: "000001.SZ", strategy: { id: "buy_hold" } },
              { stock_code: "000002.SZ", strategy: { id: "buy_hold" } },
            ],
            start_date: "2024-06-03",
            end_date: "2024-06-28",
            data_versions: versions,
          },
        });
      if (url.includes("/results"))
        return Response.json({
          summary,
          curve: [],
          items: [],
          total: 0,
          page: 1,
          page_size: 50,
          warnings: [],
          benchmark: { summary, curve: [], warnings: [] },
          excess_return: 0,
          fixture_mode: true,
          parameters: {
            ...defaults,
            members: [
              { stock_code: "000001.SZ", strategy: { id: "buy_hold" } },
              { stock_code: "000002.SZ", strategy: { id: "buy_hold" } },
            ],
            start_date: "2024-06-03",
            end_date: "2024-06-28",
            data_versions: versions,
          },
          assumptions: {},
          data_versions: versions,
        });
      if (url.includes("/positions"))
        return Response.json({
          date: "2024-06-28",
          cash: 1000,
          cash_weight: 0.01,
          items: [],
        });
      if (url.includes("/stocks/"))
        return Response.json({
          label: "000001.SZ",
          stock_code: "000001.SZ",
          prices: [],
          trades: [],
          items: [],
          total: 0,
        });
      throw new Error(`${url} ${options?.method} ${task}`);
    }),
  );
}
afterEach(() => vi.unstubAllGlobals());
describe("组合回测", () => {
  it("多股配置提交固定版本，完成后进入结果分页", async () => {
    mock();
    render(
      <MemoryRouter>
        <Portfolio />
      </MemoryRouter>,
    );
    await waitFor(() =>
      expect(screen.getByLabelText("结束交易日")).toHaveValue("2024-06-28"),
    );
    fireEvent.change(screen.getByLabelText("组合股票"), {
      target: { value: "000001.SZ 000002.SZ" },
    });
    fireEvent.keyDown(screen.getByLabelText("组合股票"), { key: "Enter" });
    await screen.findByLabelText("000001.SZ 策略");
    fireEvent.change(screen.getByLabelText("开始交易日"), {
      target: { value: "2024-06-03" },
    });
    fireEvent.click(screen.getByRole("button", { name: "开始组合回测" }));
    await screen.findByRole("table", { name: "组合回测汇总" });
    expect(screen.getByRole("tab", { name: "回测结果" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    const call = vi
      .mocked(fetch)
      .mock.calls.find(([url]) => String(url).endsWith("/portfolio/run"));
    const body = JSON.parse(String(call?.[1]?.body));
    expect(body.members).toHaveLength(2);
    expect(body.data_versions).toEqual(versions);
    expect(body.rebalance).toBe("monthly");
    expect(body.members[0].weight).toBeUndefined();
  });
  it("自定义权重合计不正确时阻止提交", async () => {
    mock();
    render(
      <MemoryRouter>
        <Portfolio />
      </MemoryRouter>,
    );
    await screen.findByText(/测试数据模式/);
    fireEvent.change(screen.getByLabelText("组合股票"), {
      target: { value: "000001.SZ 000002.SZ" },
    });
    fireEvent.keyDown(screen.getByLabelText("组合股票"), { key: "Enter" });
    fireEvent.change(await screen.findByLabelText("资金分配"), {
      target: { value: "custom" },
    });
    fireEvent.change(screen.getByLabelText("000001.SZ 权重百分比"), {
      target: { value: "20" },
    });
    fireEvent.click(screen.getByRole("button", { name: "开始组合回测" }));
    await screen.findByText("自定义权重合计必须为100%");
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(([url]) => String(url).endsWith("/portfolio/run")),
    ).toBe(false);
  });
  it("历史任务恢复原参数和结果", async () => {
    mock(true);
    render(
      <MemoryRouter initialEntries={["/portfolio?run=p1"]}>
        <Portfolio />
      </MemoryRouter>,
    );
    await screen.findByRole("table", { name: "组合回测汇总" });
    fireEvent.click(screen.getByRole("tab", { name: "组合回测" }));
    expect(screen.getByLabelText("000002.SZ 策略")).toHaveValue("buy_hold");
    expect(screen.getByLabelText("开始交易日")).toHaveValue("2024-06-03");
    expect(screen.getByLabelText("再平衡周期")).toHaveValue("monthly");
  });
});
