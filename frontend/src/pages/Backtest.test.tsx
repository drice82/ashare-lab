import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import Backtest from "./Backtest";

vi.mock("../components/DcaChart", () => ({
  default: () => <div>资产曲线</div>,
}));
const definition = {
  stock_code: "000001.SZ",
  start_date: "2024-06-03",
  end_date: "2024-06-28",
  initial_cash: "100000",
  strategy: "ma_cross",
  short_window: 20,
  long_window: 60,
  commission_rate: "0.0003",
  minimum_commission: "5",
  transfer_rate: "0.00001",
  stamp_rate: "0.0005",
  data_versions: { daily: "pinned" },
};
function mockFetch(complete = true, running = false) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.endsWith("/backtest/options"))
        return Response.json({
          data_versions: { daily: "latest" },
          calendar_start: "2019-01-01",
          trading_days: [
            "2019-06-28",
            "2021-06-28",
            "2022-06-28",
            "2023-06-28",
            "2024-06-28",
          ],
          latest_market_date: "2024-06-28",
          fixture_mode: true,
          defaults: definition,
        });
      if (url.includes("/jobs?")) return Response.json([]);
      if (url.endsWith("/backtest/compare")) return Response.json({ id: "b1" });
      if (url.endsWith("/cancel"))
        return Response.json({ status: "cancelled" });
      if (url.endsWith("/backtest/runs/b1"))
        return Response.json({
          id: "b1",
          kind: "backtest",
          status: running ? "running" : "succeeded",
          progress: 0.5,
          payload: definition,
          result: null,
          error: null,
          logs: [],
        });
      if (url.includes("/results?"))
        return Response.json({
          summary: {
            complete,
            contributed: 100000,
            cash: 1000,
            shares: 1000,
            position_value: 10000,
            receivable: 0,
            dividends: 0,
            fees: 10,
            dividend_tax_paid: 20,
            tax_liability: 0,
            total_assets: complete ? 11000 : null,
            profit: complete ? -89000 : null,
            return_rate: complete ? -0.89 : null,
            max_drawdown: complete ? 0.89 : null,
            pending_orders: 0,
            buy_count: 1,
            sell_count: 1,
            confirmed_through: "2024-06-28",
            valuation_date: "2024-06-28",
          },
          items: [],
          total: 0,
          curve: [],
          fixture_mode: true,
          warnings: complete ? [] : ["分红口径未知"],
          parameters: definition,
          data_versions: definition.data_versions,
          assumptions: {},
        });
      return Response.json([]);
    }),
  );
}
describe("单股回测", () => {
  it("默认一年，快捷设置2/3/5年且不提交任务", async () => {
    mockFetch();
    render(
      <MemoryRouter>
        <Backtest />
      </MemoryRouter>,
    );
    await waitFor(() =>
      expect(screen.getByLabelText("开始交易日")).toHaveValue("2023-06-28"),
    );
    expect(
      screen
        .getByRole("button", { name: "模拟2年" })
        .compareDocumentPosition(
          screen.getByRole("checkbox", { name: "选择买入持有" }),
        ) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    for (const [years, start] of [
      [2, "2022-06-28"],
      [3, "2021-06-28"],
      [5, "2019-06-28"],
    ]) {
      fireEvent.click(screen.getByRole("button", { name: `模拟${years}年` }));
      expect(screen.getByLabelText("开始交易日")).toHaveValue(start);
    }
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(([url]) => String(url).endsWith("/backtest/compare")),
    ).toBe(false);
  });
  it("提交策略、费用及固定版本，显示测试模式", async () => {
    mockFetch();
    render(
      <MemoryRouter>
        <Backtest />
      </MemoryRouter>,
    );
    await screen.findByText(/测试数据模式/);
    expect(
      screen.getByRole("heading", { name: "单股回测" }),
    ).toBeInTheDocument();
    expect(screen.getByText("交易费用").closest("details")).not.toHaveAttribute(
      "open",
    );
    fireEvent.change(screen.getByLabelText("股票代码"), {
      target: { value: "000001.SZ" },
    });
    fireEvent.change(screen.getByLabelText("开始交易日"), {
      target: { value: "2024-06-03" },
    });
    fireEvent.click(screen.getByRole("button", { name: "开始回测" }));
    await screen.findByText("最大回撤");
    await waitFor(() => expect(screen.getByRole("tab", { name: "回测结果" })).toHaveAttribute("aria-selected", "true"));
    expect(screen.getByRole("table", { name: "回测汇总" })).toHaveTextContent(
      "年化收益",
    );
    const call = vi
      .mocked(fetch)
      .mock.calls.find(([url]) => String(url).endsWith("/backtest/compare"));
    const body = JSON.parse(call![1]!.body as string);
    expect(body.strategies).toHaveLength(6);
    expect(
      body.strategies.find((item: { id: string }) => item.id === "ma_cross")
        .long_window,
    ).toBe(60);
    expect(body.data_versions).toEqual({ daily: "latest" });
    expect(body.stamp_rate).toBe("0.0005");
  });
  it("刷新恢复历史参数，明确未知收益", async () => {
    mockFetch(false);
    render(
      <MemoryRouter initialEntries={["/backtest?run=b1"]}>
        <Backtest />
      </MemoryRouter>,
    );
    await screen.findByText(/完整收益未知/);
    await waitFor(() =>
      expect(screen.getByLabelText("股票代码")).toHaveValue("000001.SZ"),
    );
    expect(screen.queryByDisplayValue("pinned")).not.toBeInTheDocument();
    expect(screen.queryByText(/data_versions/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "开始回测" }));
    await waitFor(() => {
      const call = vi
        .mocked(fetch)
        .mock.calls.find(([url]) => String(url).endsWith("/backtest/compare"));
      expect(JSON.parse(String(call?.[1]?.body)).data_versions).toEqual({
        daily: "pinned",
      });
    });
    expect(screen.getByText("分红口径未知")).toBeInTheDocument();
  });
  it("取消运行任务，不提前请求结果", async () => {
    mockFetch(true, true);
    render(
      <MemoryRouter initialEntries={["/backtest?run=b1"]}>
        <Backtest />
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "取消任务" }));
    await waitFor(() =>
      expect(
        vi
          .mocked(fetch)
          .mock.calls.some(([url]) => String(url).endsWith("/cancel")),
      ).toBe(true),
    );
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(([url]) => String(url).includes("/results?")),
    ).toBe(false);
  });
});

it("删除当前历史回测清除结果并保留参数", async () => {
  mockFetch();
  const base = vi.mocked(fetch).getMockImplementation()!;
  let deleted = false;
  vi.mocked(fetch).mockImplementation(async (url, options) => {
    if (options?.method === "DELETE") {
      deleted = true;
      return Response.json({ deleted: true });
    }
    if (String(url).includes("/jobs?"))
      return Response.json(
        deleted
          ? []
          : [
              {
                id: "b1",
                kind: "backtest",
                status: "succeeded",
                progress: 1,
                payload: definition,
                created_at: "2024-06-28",
                result: null,
              },
            ],
      );
    return base(url, options);
  });
  render(
    <MemoryRouter initialEntries={["/backtest?run=b1"]}>
      <Backtest />
    </MemoryRouter>,
  );
  await screen.findByText("最大回撤");
  fireEvent.click(screen.getByRole("tab", { name: "历史任务" }));
  fireEvent.click(await screen.findByRole("button", { name: "删除任务 b1" }));
  fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
  await waitFor(() =>
    expect(screen.queryByText("最大回撤")).not.toBeInTheDocument(),
  );
  fireEvent.click(screen.getByRole("tab", { name: "回测" }));
  expect(screen.getByLabelText("股票代码")).toHaveValue("000001.SZ");
  expect(
    vi
      .mocked(fetch)
      .mock.calls.some(([url]) => String(url).includes("/runs/null")),
  ).toBe(false);
});

it("任务结束后刷新历史状态，删除按钮不保留运行中状态", async () => {
  mockFetch();
  const base = vi.mocked(fetch).getMockImplementation()!;
  let reads = 0;
  vi.mocked(fetch).mockImplementation(async (url, options) => {
    if (String(url).includes("/jobs?")) {
      reads += 1;
      return Response.json([
        {
          id: "b1",
          kind: "backtest",
          status: reads === 1 ? "running" : "succeeded",
          progress: reads === 1 ? 0 : 1,
          payload: definition,
          created_at: "2024-06-28",
          result: null,
        },
      ]);
    }
    return base(url, options);
  });
  render(
    <MemoryRouter initialEntries={["/backtest?run=b1&tab=history"]}>
      <Backtest />
    </MemoryRouter>,
  );
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "删除任务 b1" })).toBeEnabled(),
  );
  expect(reads).toBeGreaterThan(1);
});

it("历史页签隔离列表并保留表单", async () => {
  mockFetch();
  render(
    <MemoryRouter>
      <Backtest />
    </MemoryRouter>,
  );
  await screen.findByText(/测试数据模式/);
  fireEvent.change(screen.getByLabelText("股票代码"), {
    target: { value: "600000.SH" },
  });
  expect(
    vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("/jobs?")),
  ).toBe(false);
  fireEvent.click(screen.getByRole("tab", { name: "历史任务" }));
  await waitFor(() =>
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(([url]) => String(url).includes("/jobs?")),
    ).toBe(true),
  );
  expect(screen.getByLabelText("股票代码")).not.toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "回测" }));
  expect(screen.getByLabelText("股票代码")).toBeVisible();
  expect(screen.getByLabelText("股票代码")).toHaveValue("600000.SH");
});

it("历史日历不同只能手动选日期，快捷按钮不替换内部版本", async () => {
  mockFetch();
  const base = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (url, options) => {
    const response = await base(url, options);
    const body = await response.json();
    if (String(url).endsWith("/backtest/options"))
      body.data_versions = { daily: "latest", trade_cal: "new-calendar" };
    if (String(url).endsWith("/backtest/runs/b1"))
      body.payload = {
        ...definition,
        data_versions: { daily: "pinned", trade_cal: "old-calendar" },
      };
    return Response.json(body);
  });
  render(
    <MemoryRouter initialEntries={["/backtest?run=b1"]}>
      <Backtest />
    </MemoryRouter>,
  );
  await waitFor(() =>
    expect(screen.getByLabelText("开始交易日")).toHaveValue("2024-06-03"),
  );
  await waitFor(() => expect(screen.getByRole("tab", { name: "回测结果" })).toHaveAttribute("aria-selected", "true"));
  fireEvent.click(screen.getByRole("tab", { name: "回测" }));
  fireEvent.click(screen.getByRole("button", { name: "模拟2年" }));
  expect(
    screen.getByText("历史任务的交易日历与当前不同，请手动选择日期。"),
  ).toBeVisible();
  expect(screen.getByLabelText("开始交易日")).toHaveValue("2024-06-03");
  fireEvent.click(screen.getByRole("button", { name: "开始回测" }));
  await waitFor(() => {
    const call = vi
      .mocked(fetch)
      .mock.calls.find(([url]) => String(url).endsWith("/backtest/compare"));
    expect(JSON.parse(String(call?.[1]?.body)).data_versions).toEqual({
      daily: "pinned",
      trade_cal: "old-calendar",
    });
  });
});
