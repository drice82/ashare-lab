import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Screening from "./Screening";

const rules = {
  rules: [
    {
      id: "A",
      name: "高股息 + 单日下跌",
      defaults: {
        dividend_min: 0.04,
        daily_return_max: -0.03,
        exclude_st: true,
        exclude_suspended: true,
      },
      schema: { properties: {} },
    },
    {
      id: "F",
      name: "通用组合条件",
      defaults: {
        group: {
          logic: "AND",
          children: [
            {
              factor: "daily_return",
              operator: "gte",
              value: 0,
              enabled: true,
            },
          ],
        },
      },
      schema: { properties: {} },
    },
  ],
  factors: [{ id: "daily_return", name: "当日涨跌幅", unit: "%" }],
};
describe("筛选主要流程", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, options?: RequestInit) => {
        if (url.includes("/jobs?")) return Response.json([]);
        if (url.endsWith("/screening/rules")) return Response.json(rules);
        if (url.endsWith("/market/status"))
          return Response.json({ latest_market_date: "2024-06-28" });
        if (url.endsWith("/screening/presets"))
          return Response.json(options?.method === "POST" ? { id: "p1" } : []);
        if (url.endsWith("/screening/run"))
          return Response.json({ id: "run-1" });
        if (url.endsWith("/screening/runs/run-1"))
          return Response.json({
            id: "run-1",
            status: "succeeded",
            progress: 1,
            payload: {
              as_of_date: "2024-06-28",
              rule_id: "A",
              universe: { type: "all", codes: [] },
              parameters: rules.rules[0].defaults,
            },
            error: null,
          });
        if (url.includes("/results?"))
          return Response.json({
            items: [
              {
                stock_code: "000001.SZ",
                stock_name: "测试证券",
                latest_trade_date: "2024-06-28",
                rule_id: "A",
                match_status: "matched",
                factor_values: {
                  close: 10,
                  daily_return: -0.04,
                  dividend_yield_ttm: 0.06,
                },
              },
            ],
            total: 1,
            summary: { matched: 1, insufficient_data: 0 },
          });
        return Response.json({});
      }),
    );
  });
  it.each(["平安", "000001"])("默认股票池可用名称或代码 %s 选择并提交筛选", async (search) => {
    const originalFetch = fetch;
    vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
      if (url.includes("/instruments?"))
        return Response.json({ items: [{ stock_code: "000001.SZ", name: "平安银行" }] });
      return originalFetch(url, options);
    }));
    render(<MemoryRouter><Screening /></MemoryRouter>);
    const input = await screen.findByRole("combobox", { name: "股票名称或代码" });
    expect(screen.getByLabelText("股票池")).toHaveValue("all");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: search } });
    fireEvent.click(await screen.findByRole("option", { name: /平安银行/ }));
    expect(screen.getByLabelText("股票池")).toHaveValue("custom");
    fireEvent.click(screen.getByRole("button", { name: "执行筛选" }));
    await waitFor(() => {
      const call = vi.mocked(fetch).mock.calls.find(([url, opts]) => String(url).endsWith("/screening/run") && opts?.method === "POST");
      expect(JSON.parse(String(call?.[1]?.body)).universe).toEqual({ type: "custom", codes: ["000001.SZ"] });
    });
    const lookup = vi.mocked(fetch).mock.calls.find(([url]) => String(url).includes("/instruments?"));
    expect(String(lookup?.[0])).toContain("as_of_date=2024-06-28");
  });
  it("返回全市场后清空股票选择", async () => {
    render(<MemoryRouter><Screening /></MemoryRouter>);
    const input = await screen.findByRole("combobox", { name: "股票名称或代码" });
    fireEvent.paste(input, { clipboardData: { getData: () => "000001.SZ, 600000.SH" } });
    expect(screen.getByLabelText("股票池")).toHaveValue("custom");
    expect(screen.getByRole("button", { name: "删除 000001.SZ" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("股票池"), { target: { value: "all" } });
    expect(screen.queryByRole("button", { name: /删除/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "执行筛选" }));
    await waitFor(() => {
      const call = vi.mocked(fetch).mock.calls.find(([url, opts]) => String(url).endsWith("/screening/run") && opts?.method === "POST");
      expect(JSON.parse(String(call?.[1]?.body)).universe).toEqual({ type: "all", codes: [] });
    });
  });
  it("独立近期分红与可调登记窗口", async () => {
    render(
      <MemoryRouter>
        <Screening />
      </MemoryRouter>,
    );
    await screen.findByLabelText("筛选方式");
    fireEvent.change(screen.getByLabelText("筛选方式"), {
      target: { value: "dividend" },
    });
    expect(screen.queryByLabelText("最低股息率")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("登记窗口天数"), {
      target: { value: "14" },
    });
    fireEvent.click(screen.getByRole("button", { name: "执行筛选" }));
    await waitFor(() => {
      const call = vi
        .mocked(fetch)
        .mock.calls.find(
          ([url, opts]) =>
            String(url).endsWith("/screening/run") && opts?.method === "POST",
        );
      const body = JSON.parse(String(call?.[1]?.body));
      expect(body.mode).toBe("dividend");
      expect(body.rule_id).toBeNull();
      expect(body.signal_filter).toBeNull();
      expect(body.dividend_filter).toEqual({ days: 14 });
    });
  });
  it("修改参数、执行异步筛选并显示结果", async () => {
    render(
      <MemoryRouter>
        <Screening />
      </MemoryRouter>,
    );
    await screen.findByLabelText("最低股息率");
    expect(screen.getByLabelText("最低股息率")).toHaveAttribute("step", "0.1");
    fireEvent.change(screen.getByLabelText("最低股息率"), {
      target: { value: "3.5" },
    });
    fireEvent.click(screen.getByRole("button", { name: "执行筛选" }));
    await screen.findByText("测试证券");
    const call = vi
      .mocked(fetch)
      .mock.calls.find(([url]) => String(url).endsWith("/screening/run"));
    expect(JSON.parse(String(call?.[1]?.body)).parameters.dividend_min).toBe(
      0.035,
    );
    expect(screen.getByText("-4.00%")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "筛选结果" })).toHaveAttribute(
        "aria-selected",
        "true",
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: /收盘价/ }));
    await waitFor(() =>
      expect(
        vi
          .mocked(fetch)
          .mock.calls.some(([url]) => String(url).includes("sort_by=close")),
      ).toBe(true),
    );
  });
  it("保存方案并展示组合条件编辑器", async () => {
    render(
      <MemoryRouter>
        <Screening />
      </MemoryRouter>,
    );
    await screen.findByLabelText("最低股息率");
    fireEvent.change(screen.getByLabelText("方案名称"), {
      target: { value: "我的高股息" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存方案" }));
    await screen.findByText("筛选方案已保存");
    fireEvent.change(screen.getByLabelText("筛选规则"), {
      target: { value: "F" },
    });
    expect(screen.getByLabelText("第1层组合逻辑")).toHaveValue("AND");
    fireEvent.change(screen.getByLabelText("第1层组合逻辑"), {
      target: { value: "OR" },
    });
    expect(screen.getByLabelText("第1层组合逻辑")).toHaveValue("OR");
    expect(screen.getByLabelText("启用条件1")).toBeChecked();
  });
  it("历史页签独立展示且保留筛选条件", async () => {
    render(
      <MemoryRouter>
        <Screening />
      </MemoryRouter>,
    );
    await screen.findByLabelText("最低股息率");
    fireEvent.change(screen.getByLabelText("最低股息率"), {
      target: { value: "7" },
    });
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(([url]) => String(url).includes("/jobs?")),
    ).toBe(false);
    fireEvent.click(screen.getByRole("tab", { name: "历史任务" }));
    await screen.findByText(/暂无任务/);
    expect(screen.getByLabelText("最低股息率")).not.toBeVisible();
    fireEvent.click(screen.getByRole("tab", { name: "股票筛选" }));
    expect(
      Number((screen.getByLabelText("最低股息率") as HTMLInputElement).value),
    ).toBeCloseTo(7);
  });
  it("服务失败显示实际错误", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({ message: "数据未就绪" }, { status: 409 }),
      ),
    );
    render(
      <MemoryRouter>
        <Screening />
      </MemoryRouter>,
    );
    expect(
      (await screen.findAllByRole("alert")).every(
        (alert) => alert.textContent === "数据未就绪",
      ),
    ).toBe(true);
  });
  it("独立信号筛选支持多选、方向和方案保存，隐藏规则参数", async () => {
    render(
      <MemoryRouter>
        <Screening />
      </MemoryRouter>,
    );
    await screen.findByLabelText("筛选方式");
    fireEvent.change(screen.getByLabelText("筛选方式"), {
      target: { value: "signal" },
    });
    expect(screen.queryByLabelText("最低股息率")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("选择买入持有")).not.toBeInTheDocument();
    expect(screen.getByLabelText("选择双均线交叉")).toBeChecked();
    fireEvent.click(screen.getByLabelText("选择MACD交叉"));
    fireEvent.change(screen.getByLabelText("信号方向"), {
      target: { value: "either" },
    });
    fireEvent.change(screen.getByLabelText("方案名称"), {
      target: { value: "买卖信号" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存方案" }));
    await screen.findByText("筛选方案已保存");
    const saved = vi
      .mocked(fetch)
      .mock.calls.find(
        ([url, init]) =>
          String(url).endsWith("/screening/presets") && init?.method === "POST",
      );
    const definition = JSON.parse(String(saved?.[1]?.body)).definition;
    expect(definition.rule_id).toBeNull();
    expect(definition.parameters).toEqual({
      exclude_st: false,
      exclude_suspended: true,
    });
    expect(definition.signal_filter.direction).toBe("either");
    expect(
      definition.signal_filter.strategies.map((s: { id: string }) => s.id),
    ).toEqual(["ma_cross", "macd_cross"]);
    fireEvent.change(screen.getByLabelText("筛选方式"), {
      target: { value: "rule_signal" },
    });
    expect(screen.getByLabelText("最低股息率")).toHaveValue("4");
  });
  it("历史信号任务恢复原策略参数及方向，空选择禁止执行", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/screening/rules")) return Response.json(rules);
        if (url.endsWith("/market/status"))
          return Response.json({
            latest_market_date: "2026-10-09",
            fixture_mode: true,
          });
        if (url.endsWith("/screening/runs/saved"))
          return Response.json({
            id: "saved",
            status: "succeeded",
            progress: 1,
            kind: "screening",
            payload: {
              mode: "signal",
              as_of_date: "2024-06-28",
              rule_id: null,
              parameters: { exclude_st: true, exclude_suspended: false },
              universe: { type: "custom", codes: ["000001.SZ"] },
              signal_filter: {
                direction: "sell",
                strategies: [
                  { id: "ma_cross", short_window: 2, long_window: 5 },
                ],
              },
            },
          });
        if (url.includes("/results?"))
          return Response.json({
            items: [],
            total: 0,
            summary: { matched: 0, total: 1, insufficient_data: 0 },
          });
        return Response.json([]);
      }),
    );
    render(
      <MemoryRouter initialEntries={["/screening?run=saved"]}>
        <Screening />
      </MemoryRouter>,
    );
    await screen.findByRole("tab", { name: "筛选结果", selected: true });
    fireEvent.click(screen.getByRole("tab", { name: "股票筛选" }));
    expect(screen.getByLabelText("筛选方式")).toHaveValue("signal");
    expect(screen.getByLabelText("筛选日期")).toHaveValue("2024-06-28");
    expect(screen.getByLabelText("信号方向")).toHaveValue("sell");
    expect(screen.getByLabelText("剔除ST")).toBeChecked();
    fireEvent.click(screen.getByText("双均线交叉参数"));
    expect(screen.getByLabelText("双均线交叉 短周期")).toHaveValue(2);
    expect(screen.getByLabelText("双均线交叉 长周期")).toHaveValue(5);
    fireEvent.click(screen.getByLabelText("选择双均线交叉"));
    expect(screen.getByRole("button", { name: "执行筛选" })).toBeDisabled();
  });
  it("带历史页签打开已完成任务时不自动跳回结果", async () => {
    render(
      <MemoryRouter initialEntries={["/screening?run=run-1&tab=history"]}>
        <Screening />
      </MemoryRouter>,
    );
    await screen.findByText(/暂无任务/);
    await waitFor(() =>
      expect(screen.getByLabelText("最低股息率")).toHaveValue("4"),
    );
    expect(screen.getByRole("tab", { name: "历史任务" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });
});

it("保留小数点编辑并拒绝超过一位小数的股息率", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.endsWith("/screening/rules"))
        return Response.json({
          rules: [
            {
              id: "A",
              name: "股息",
              defaults: { dividend_min: 0.03 },
              schema: {
                properties: { dividend_min: { minimum: 0, maximum: 1 } },
              },
            },
          ],
          factors: [],
        });
      if (url.endsWith("/market/status"))
        return Response.json({
          latest_market_date: "2026-10-09",
          datasets: {},
        });
      return Response.json([]);
    }),
  );
  render(
    <MemoryRouter>
      <Screening />
    </MemoryRouter>,
  );
  const input = (await screen.findByLabelText(
    "最低股息率",
  )) as HTMLInputElement;
  fireEvent.change(input, { target: { value: "3." } });
  expect(input.value).toBe("3.");
  fireEvent.change(input, { target: { value: "3.55" } });
  expect(input.checkValidity()).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "执行筛选" }));
  expect(
    vi
      .mocked(fetch)
      .mock.calls.some(([url]) => String(url).endsWith("/screening/run")),
  ).toBe(false);
  fireEvent.change(input, { target: { value: "3.5" } });
  expect(input.checkValidity()).toBe(true);
  fireEvent.blur(input);
  expect(input.value).toBe("3.5");
});
