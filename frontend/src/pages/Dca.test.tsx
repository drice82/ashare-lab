import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import Dca from "./Dca";

vi.mock("../components/DcaChart", () => ({
  default: () => <div>资产曲线</div>,
}));
const definition = {
  stock_code: "000001.SZ",
  start_date: "2024-06-03",
  end_date: "2024-06-28",
  monthly_day: 3,
  monthly_amount: "3000",
  commission_rate: "0.0003",
  minimum_commission: "5",
  transfer_rate: "0.00001",
  data_versions: { daily: "pinned" },
};
function mockFetch(complete = true, running = false) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, options?: RequestInit) => {
      if (url.endsWith("/dca/options"))
        return Response.json({
          data_versions: { daily: "latest" },
          latest_market_date: "2024-06-28",
          calendar_start: "2019-01-01",
          trading_days: [
            "2019-06-28",
            "2021-06-28",
            "2022-06-28",
            "2023-06-29",
            "2024-06-28",
          ],
          fixture_mode: true,
          defaults: definition,
        });
      if (url.includes("/jobs?")) return Response.json([]);
      if (url.endsWith("/dca/run")) return Response.json({ id: "d1" });
      if (url.endsWith("/cancel"))
        return Response.json({ status: "cancelled" });
      if (url.endsWith("/dca/runs/d1"))
        return Response.json({
          id: "d1",
          kind: "dca",
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
            contributed: 3000,
            cash: 594.98,
            shares: 200,
            position_value: 2000,
            receivable: 0,
            dividends: 0,
            fees: 5.02,
            total_assets: complete ? 2594.98 : null,
            profit: complete ? -405.02 : null,
            return_rate: complete ? -0.135 : null,
            annualized_return: complete ? -0.18 : null,
            max_drawdown: complete ? 0.2 : null,
            confirmed_through: "2024-06-28",
            valuation_date: "2024-06-28",
            pending_plans: 0,
          },
          items: [
            {
              date: "2024-06-03",
              event: "buy",
              shares_delta: 200,
              price: 12,
              amount: -2400,
              fee: 5.02,
              cash: 594.98,
              shares: 200,
              planned_dates: ["2024-06-03"],
              reason: "",
            },
          ],
          total: 1,
          curve: [],
          fixture_mode: true,
          warnings: complete ? [] : ["分红口径未知"],
          data_versions: definition.data_versions,
          parameters: definition,
          assumptions: { tax_policy: "未卖出，未计未来补税" },
        });
      return Response.json(options?.method === "POST" ? {} : []);
    }),
  );
}

describe("定投模拟", () => {
  it("默认一年并按交易日设置两年、三年、五年区间", async () => {
    mockFetch();
    render(
      <MemoryRouter>
        <Dca />
      </MemoryRouter>,
    );
    await waitFor(() =>
      expect(screen.getByLabelText("开始交易日")).toHaveValue("2023-06-29"),
    );
    for (const [years, start] of [
      [3, "2021-06-28"],
      [5, "2019-06-28"],
      [2, "2022-06-28"],
    ]) {
      fireEvent.click(screen.getByRole("button", { name: `模拟${years}年` }));
      expect(screen.getByLabelText("开始交易日")).toHaveValue(start);
      expect(screen.getByLabelText("结束交易日")).toHaveValue("2024-06-28");
    }
    fireEvent.change(screen.getByLabelText("结束交易日"), {
      target: { value: "2020-06-28" },
    });
    fireEvent.click(screen.getByRole("button", { name: "模拟5年" }));
    expect(screen.getByText(/交易日历不足/)).toBeInTheDocument();
    expect(screen.getByLabelText("开始交易日")).toHaveValue("2022-06-28");
  });
  it("显式提交版本、显示账本与测试模式", async () => {
    mockFetch();
    render(
      <MemoryRouter>
        <Dca />
      </MemoryRouter>,
    );
    await screen.findByText(/测试数据模式/);
    expect(screen.getByText("交易费用").closest("details")).not.toHaveAttribute(
      "open",
    );
    fireEvent.change(screen.getByLabelText("股票代码"), {
      target: { value: "000001.SZ" },
    });
    fireEvent.change(screen.getByLabelText("开始交易日"), {
      target: { value: "2024-06-03" },
    });
    fireEvent.click(screen.getByRole("button", { name: "开始模拟" }));
    await screen.findByText("买入");
    const call = vi
      .mocked(fetch)
      .mock.calls.find(([url]) => String(url).endsWith("/dca/run"));
    expect(JSON.parse(call![1]!.body as string).data_versions).toEqual({
      daily: "latest",
    });
    await waitFor(() => expect(screen.getByRole("tab", { name: "模拟结果" })).toHaveAttribute("aria-selected", "true"));
    expect(screen.getByRole("table", { name: "定投汇总" })).toContainElement(
      screen.getByText("累计投入"),
    );
    expect(screen.getByText("-18.00%")).toHaveClass("negative");
    expect(screen.getByText("20.00%")).toHaveClass("negative");
    expect(screen.getByText("买入")).toHaveClass("bg-violet-100");
    expect(screen.getByText("3,000 元")).toHaveClass("neutral");
    expect(screen.getByText("-405.02 元")).toHaveClass("negative");
    expect(screen.getByText("-13.50%")).toHaveClass("negative");
  });
  it("刷新历史任务时恢复原版本并明确未知收益", async () => {
    mockFetch(false);
    render(
      <MemoryRouter initialEntries={["/dca?run=d1"]}>
        <Dca />
      </MemoryRouter>,
    );
    await screen.findByText("分红口径未知");
    expect(screen.getByText(/完整收益未知/)).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByLabelText("股票代码")).toHaveValue("000001.SZ"),
    );
    expect(screen.queryByDisplayValue("pinned")).not.toBeInTheDocument();
    expect(screen.queryByText(/data_versions/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "开始模拟" }));
    await waitFor(() => {
      const call = vi
        .mocked(fetch)
        .mock.calls.find(([url]) => String(url).endsWith("/dca/run"));
      expect(JSON.parse(String(call?.[1]?.body)).data_versions).toEqual({
        daily: "pinned",
      });
    });
    expect(screen.getByLabelText("开始交易日")).toHaveValue("2024-06-03");
    expect(screen.getByLabelText("结束交易日")).toHaveValue("2024-06-28");
  });
  it("运行中可以取消且不请求未发布结果", async () => {
    mockFetch(true, true);
    render(
      <MemoryRouter initialEntries={["/dca?run=d1"]}>
        <Dca />
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

it("历史页签隔离列表并保留表单", async () => {
  mockFetch();
  render(
    <MemoryRouter>
      <Dca />
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
  fireEvent.click(screen.getByRole("tab", { name: "定投模拟" }));
  expect(screen.getByLabelText("股票代码")).toBeVisible();
  expect(screen.getByLabelText("股票代码")).toHaveValue("600000.SH");
});

it("历史日历不同只能手动选日期，快捷按钮不替换内部版本", async () => {
  mockFetch();
  const base = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (url, options) => {
    const response = await base(url, options);
    const body = await response.json();
    if (String(url).endsWith("/dca/options"))
      body.data_versions = { daily: "latest", trade_cal: "new-calendar" };
    if (String(url).endsWith("/dca/runs/d1"))
      body.payload = {
        ...definition,
        data_versions: { daily: "pinned", trade_cal: "old-calendar" },
      };
    return Response.json(body);
  });
  render(
    <MemoryRouter initialEntries={["/dca?run=d1"]}>
      <Dca />
    </MemoryRouter>,
  );
  await waitFor(() =>
    expect(screen.getByLabelText("开始交易日")).toHaveValue("2024-06-03"),
  );
  await waitFor(() => expect(screen.getByRole("tab", { name: "模拟结果" })).toHaveAttribute("aria-selected", "true"));
  fireEvent.click(screen.getByRole("tab", { name: "定投模拟" }));
  fireEvent.click(screen.getByRole("button", { name: "模拟2年" }));
  expect(
    screen.getByText("历史任务的交易日历与当前不同，请手动选择日期。"),
  ).toBeVisible();
  expect(screen.getByLabelText("开始交易日")).toHaveValue("2024-06-03");
  fireEvent.click(screen.getByRole("button", { name: "开始模拟" }));
  await waitFor(() => {
    const call = vi
      .mocked(fetch)
      .mock.calls.find(([url]) => String(url).endsWith("/dca/run"));
    expect(JSON.parse(String(call?.[1]?.body)).data_versions).toEqual({
      daily: "pinned",
      trade_cal: "old-calendar",
    });
  });
});
