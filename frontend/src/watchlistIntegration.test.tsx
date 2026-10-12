import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { UserSessionProvider } from "./userSession";
import { WatchlistProvider, localListsKey } from "./watchlists";
import Screening from "./pages/Screening";
import Portfolio from "./pages/Portfolio";

vi.mock("./components/PortfolioCharts", () => ({ default: () => null }));
vi.mock("./components/ComparisonCharts", () => ({ TradeChart: () => null }));
const defaults = {
  initial_cash: "100000",
  allocation: "equal",
  rebalance: "monthly",
  commission_rate: "0.0003",
  minimum_commission: "5",
  transfer_rate: "0.00001",
  stamp_rate: "0.0005",
};
const versions = { stock_basic: "basic", trade_cal: "cal", daily: "prices" };
beforeEach(() => {
  localStorage.clear();
  const stamp = "2026-10-12T00:00:00Z";
  localStorage.setItem(
    localListsKey,
    JSON.stringify([
      {
        id: "local",
        name: "银行",
        codes: ["000001.SZ", "600000.SH"],
        revision: 1,
        created_at: stamp,
        updated_at: stamp,
      },
      {
        id: "empty",
        name: "空列表",
        codes: [],
        revision: 1,
        created_at: stamp,
        updated_at: stamp,
      },
      {
        id: "large",
        name: "超限",
        codes: Array.from(
          { length: 21 },
          (_, i) => `600${String(i).padStart(3, "0")}.SH`,
        ),
        revision: 1,
        created_at: stamp,
        updated_at: stamp,
      },
    ]),
  );
  let payload: unknown;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, options?: RequestInit) => {
      if (url.endsWith("/notifications/session"))
        return Response.json({ message: "未登录" }, { status: 401 });
      if (url.includes("/instruments")) return Response.json({ items: [] });
      if (url.includes("/jobs?")) return Response.json([]);
      if (url.endsWith("/market/status"))
        return Response.json({ latest_market_date: "2024-06-28" });
      if (url.endsWith("/screening/presets")) return Response.json([]);
      if (url.endsWith("/screening/rules"))
        return Response.json({
          rules: [
            {
              id: "A",
              name: "规则A",
              defaults: {
                dividend_min: 0.04,
                exclude_st: true,
                exclude_suspended: true,
              },
              schema: { properties: {} },
            },
          ],
          factors: [],
        });
      if (url.endsWith("/portfolio/options"))
        return Response.json({
          defaults,
          fixture_mode: true,
          data_versions: versions,
          latest_market_date: "2024-06-28",
          trading_days: ["2024-06-03", "2024-06-28"],
          calendar_start: "2024-06-03",
        });
      if (url.endsWith("/run")) {
        payload = JSON.parse(String(options?.body));
        return Response.json({ id: "new-run" }, { status: 202 });
      }
      if (url.endsWith("/runs/new-run"))
        return Response.json({
          id: "new-run",
          status: "queued",
          payload,
          progress: 0,
        });
      throw new Error(`Unexpected test endpoint: ${url}`);
    }),
  );
});
function mount(page: "screening" | "portfolio") {
  render(
    <MemoryRouter>
      <UserSessionProvider>
        <WatchlistProvider>
          {page === "screening" ? <Screening /> : <Portfolio />}
        </WatchlistProvider>
      </UserSessionProvider>
    </MemoryRouter>,
  );
}

it.each(["rule", "signal", "rule_signal", "dividend"])(
  "自选列表用于%s筛选并保存明确代码快照",
  async (mode) => {
    mount("screening");
    await screen.findByLabelText("股票池");
    fireEvent.change(screen.getByLabelText("筛选方式"), {
      target: { value: mode },
    });
    fireEvent.change(screen.getByLabelText("股票池"), {
      target: { value: "watchlist" },
    });
    fireEvent.change(screen.getByLabelText("选择自选列表"), {
      target: { value: "local:local" },
    });
    fireEvent.click(screen.getByRole("button", { name: "载入自选列表" }));
    // Editing the research copy must leave the persisted list unchanged.
    fireEvent.click(screen.getByRole("button", { name: "删除 600000.SH" }));
    fireEvent.click(screen.getByRole("button", { name: "执行筛选" }));
    await waitFor(() => {
      const call = vi
        .mocked(fetch)
        .mock.calls.find(
          ([url, options]) =>
            String(url).endsWith("/screening/run") &&
            options?.method === "POST",
        );
      const body = JSON.parse(String(call?.[1]?.body));
      expect(body.universe).toEqual({ type: "custom", codes: ["000001.SZ"] });
      expect(body.mode).toBe(mode);
      expect(body.watchlist_id).toBeUndefined();
    });
    expect(JSON.parse(localStorage.getItem(localListsKey)!)[0].codes).toEqual([
      "000001.SZ",
      "600000.SH",
    ]);
  },
);

it("组合替换确认、取消、重置逐股配置，保留共同参数", async () => {
  mount("portfolio");
  await waitFor(() =>
    expect(screen.getByLabelText("结束交易日")).toHaveValue("2024-06-28"),
  );
  fireEvent.change(screen.getByLabelText("开始交易日"), {
    target: { value: "2024-06-03" },
  });
  fireEvent.change(screen.getByLabelText("组合股票"), {
    target: { value: "000002.SZ" },
  });
  fireEvent.keyDown(screen.getByLabelText("组合股票"), { key: "Enter" });
  await screen.findByLabelText("000002.SZ 策略");
  fireEvent.change(screen.getByLabelText("资金分配"), {
    target: { value: "custom" },
  });
  fireEvent.change(screen.getByLabelText("选择自选列表"), {
    target: { value: "local:local" },
  });
  fireEvent.click(screen.getByRole("button", { name: "应用自选列表" }));
  expect(
    screen.getByRole("alertdialog", { name: "替换组合确认" }),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "取消替换" }));
  expect(screen.getByLabelText("000002.SZ 策略")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "应用自选列表" }));
  fireEvent.click(screen.getByRole("button", { name: "确认替换" }));
  expect(screen.queryByLabelText("000002.SZ 策略")).not.toBeInTheDocument();
  expect(screen.getByLabelText("000001.SZ 策略")).toHaveValue("buy_hold");
  expect(screen.getByLabelText("资金分配")).toHaveValue("equal");
  fireEvent.click(screen.getByRole("button", { name: "开始组合回测" }));
  await waitFor(() => {
    const call = vi
      .mocked(fetch)
      .mock.calls.find(([url]) => String(url).endsWith("/portfolio/run"));
    const body = JSON.parse(String(call?.[1]?.body));
    expect(body.members).toEqual([
      { stock_code: "000001.SZ", strategy: { id: "buy_hold" } },
      { stock_code: "600000.SH", strategy: { id: "buy_hold" } },
    ]);
    expect(body.start_date).toBe("2024-06-03");
    expect(body.initial_cash).toBe("100000");
    expect(body.rebalance).toBe("monthly");
    expect(body.data_versions).toEqual(versions);
  });
});

it("空列表和21只列表均不能替换已有组合", async () => {
  mount("portfolio");
  await waitFor(() =>
    expect(screen.getByLabelText("结束交易日")).toHaveValue("2024-06-28"),
  );
  fireEvent.change(screen.getByLabelText("组合股票"), {
    target: { value: "000002.SZ" },
  });
  fireEvent.keyDown(screen.getByLabelText("组合股票"), { key: "Enter" });
  for (const id of ["empty", "large"]) {
    fireEvent.change(screen.getByLabelText("选择自选列表"), {
      target: { value: `local:${id}` },
    });
    expect(screen.getByRole("button", { name: "应用自选列表" })).toBeDisabled();
    expect(screen.getByLabelText("000002.SZ 策略")).toBeInTheDocument();
  }
  expect(screen.getByText(/请先调整自选列表/)).toBeInTheDocument();
});
