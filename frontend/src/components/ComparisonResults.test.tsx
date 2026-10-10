import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import ComparisonResultsView from "./ComparisonResults";
import {
  strategies,
  type ComparisonResults,
  type StrategyResult,
} from "../comparison";
import type { BacktestSummary } from "../types";

vi.mock("./ComparisonCharts", () => ({
  ComparisonChart: ({
    results,
    field,
  }: {
    results: StrategyResult[];
    field: string;
  }) => <div data-testid={field}>曲线数量 {results.length}</div>,
  MetricBars: () => <div>指标柱状图</div>,
  TradeChart: () => <div>股价与买卖点图</div>,
}));
const summary: BacktestSummary = {
  complete: true,
  contributed: 100000,
  total_assets: 110000,
  profit: 10000,
  return_rate: 0.1,
  annualized_return: 0.1,
  max_drawdown: 0.05,
  cash: 1000,
  shares: 1000,
  position_value: 109000,
  receivable: 0,
  tax_liability: 0,
  dividends: 0,
  fees: 10,
  dividend_tax_paid: 0,
  pending_orders: 0,
  pending_order: null,
  buy_count: 1,
  sell_count: 0,
  confirmed_through: "2024-06-28",
  valuation_date: "2024-06-28",
};
const results: StrategyResult[] = strategies.slice(0, 3).map((item, index) => ({
  strategy_id: item.id,
  label: item.label,
  parameters: { id: item.id, ...item.defaults },
  summary: { ...summary, return_rate: 0.1 + index / 10 },
  curve: [
    {
      date: "2024-06-28",
      total_assets: 110000,
      return_rate: 0.1,
      drawdown: -0.05,
    },
  ],
  warnings: [],
  assumptions: { execution: "次日开盘模拟" },
  excess_return: index / 10,
}));
const data: ComparisonResults = {
  mode: "comparison",
  summary: { complete: true, strategy_count: 3, complete_count: 3 },
  strategies: results,
  fixture_mode: true,
  data_versions: { daily: "pinned" },
  warnings: [],
  parameters: {
    stock_code: "000001.SZ",
    start_date: "2024-06-03",
    end_date: "2024-06-28",
    initial_cash: "100000",
    commission_rate: "0.0003",
    minimum_commission: "5",
    transfer_rate: "0.00001",
    stamp_rate: "0.0005",
    data_versions: { daily: "pinned" },
    strategies: results.map((item) => item.parameters),
  },
};

it("收益排名、图例显隐、策略切换及全量导出入口", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const strategy = results.find((item) =>
        url.includes(`/strategies/${item.strategy_id}`),
      )!;
      return Response.json({
        ...strategy,
        prices: [],
        trades: [],
        items: [],
        total: 0,
        page: 1,
        page_size: 50,
      });
    }),
  );
  render(<ComparisonResultsView data={data} runId="compare-1" />);
  await screen.findByText("股价与买卖点图");
  expect(screen.getByTestId("return_rate")).toHaveTextContent("曲线数量 3");
  const rows = screen
    .getByRole("table", { name: "策略对比汇总" })
    .querySelectorAll("tbody tr");
  expect(rows[0]).toHaveTextContent("MACD交叉");
  fireEvent.click(screen.getByRole("button", { name: "双均线交叉" }));
  expect(screen.getByTestId("return_rate")).toHaveTextContent("曲线数量 2");
  expect(screen.getByRole("button", { name: "双均线交叉" })).toHaveClass(
    "line-through",
  );
  expect(screen.getByRole("button", { name: "双均线交叉" })).toHaveAttribute(
    "aria-pressed",
    "false",
  );
  expect(screen.getByRole("button", { name: "双均线交叉" }).querySelector("svg")).toBeNull();
  expect(screen.getByTestId("drawdown")).toHaveTextContent("曲线数量 2");
  fireEvent.click(screen.getByRole("radio", { name: "双均线交叉" }));
  expect(screen.getByRole("radio", { name: "双均线交叉" })).toBeChecked();
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  expect(screen.queryByText(/data_versions/)).not.toBeInTheDocument();
  await waitFor(() =>
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(([url]) =>
          String(url).includes("/strategies/ma_cross?page=1"),
        ),
    ).toBe(true),
  );
  expect(
    screen.getByRole("button", { name: "导出指标汇总 CSV" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "导出每日曲线 CSV" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "导出完整账本 CSV" }),
  ).toBeInTheDocument();
});

it("不完整策略置后并展示未知及原因", async () => {
  const unknown = {
    ...results[0],
    summary: {
      ...summary,
      complete: false,
      return_rate: null,
      annualized_return: null,
      total_assets: null,
      profit: null,
      max_drawdown: null,
    },
    excess_return: null,
    warnings: ["预热交易日不足"],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        ...unknown,
        prices: [],
        trades: [],
        items: [],
        total: 0,
        page: 1,
        page_size: 50,
      }),
    ),
  );
  render(
    <ComparisonResultsView
      data={{
        ...data,
        summary: { complete: false, strategy_count: 2, complete_count: 1 },
        strategies: [unknown, results[1]],
      }}
      runId="compare-2"
    />,
  );
  expect(await screen.findByText("预热交易日不足")).toBeInTheDocument();
  expect(screen.getByText(/部分策略完整收益未知/)).toBeInTheDocument();
  const rows = screen
    .getByRole("table", { name: "策略对比汇总" })
    .querySelectorAll("tbody tr");
  expect(rows[1]).toHaveTextContent("数据不足");
  expect(rows[1]).toHaveTextContent("无可用基准");
  fireEvent.click(screen.getByRole("button", { name: "累计收益率" }));
  expect(
    screen
      .getByRole("table", { name: "策略对比汇总" })
      .querySelectorAll("tbody tr")[1],
  ).toHaveTextContent("数据不足");
  fireEvent.click(screen.getByRole("button", { name: "最大回撤" }));
  expect(
    screen
      .getByRole("table", { name: "策略对比汇总" })
      .querySelectorAll("tbody tr")[1],
  ).toHaveTextContent("数据不足");
});

it("表头反复点击切换方向，所有曲线隐藏后可恢复", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        ...results[0],
        prices: [],
        trades: [],
        items: [],
        total: 0,
        page: 1,
        page_size: 50,
      }),
    ),
  );
  render(<ComparisonResultsView data={data} runId="sorting" />);
  await screen.findByText("股价与买卖点图");
  const table = screen.getByRole("table", { name: "策略对比汇总" });
  const header = screen.getByRole("button", { name: "累计收益率" });
  expect(header.closest("th")).toHaveAttribute("aria-sort", "descending");
  fireEvent.click(header);
  expect(header.closest("th")).toHaveAttribute("aria-sort", "ascending");
  expect(table.querySelector("tbody tr")).toHaveTextContent("买入持有");
  fireEvent.click(header);
  expect(table.querySelector("tbody tr")).toHaveTextContent("MACD交叉");
  const drawdown = screen.getByRole("button", { name: "最大回撤" });
  fireEvent.click(drawdown);
  expect(drawdown.closest("th")).toHaveAttribute("aria-sort", "ascending");
  fireEvent.click(drawdown);
  expect(drawdown.closest("th")).toHaveAttribute("aria-sort", "descending");
  for (const item of results)
    fireEvent.click(screen.getByRole("button", { name: item.label }));
  expect(
    screen.getByText("所有策略曲线已隐藏，请点击策略按钮重新显示。"),
  ).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "买入持有" }));
  expect(screen.getByTestId("return_rate")).toHaveTextContent("曲线数量 1");
});

it("切换详情单选将账本页码归一", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      Response.json({
        ...results.find((item) => url.includes(item.strategy_id)),
        prices: [],
        trades: [],
        items: [],
        total: 100,
        page: 1,
        page_size: 50,
      }),
    ),
  );
  render(<ComparisonResultsView data={data} runId="paging" />);
  await screen.findByText("共 100 条 · 第 1 页");
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await waitFor(() =>
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(([url]) => String(url).includes("buy_hold?page=2")),
    ).toBe(true),
  );
  fireEvent.click(screen.getByRole("radio", { name: "MACD交叉" }));
  await waitFor(() =>
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(([url]) => String(url).includes("macd_cross?page=1")),
    ).toBe(true),
  );
});
