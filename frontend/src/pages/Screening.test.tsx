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
  it("修改参数、执行异步筛选并显示结果", async () => {
    render(
      <MemoryRouter>
        <Screening />
      </MemoryRouter>,
    );
    await screen.findByLabelText("最低股息率");
    fireEvent.change(screen.getByLabelText("最低股息率"), {
      target: { value: "5" },
    });
    fireEvent.click(screen.getByRole("button", { name: "执行筛选" }));
    await screen.findByText("测试证券");
    const call = vi
      .mocked(fetch)
      .mock.calls.find(([url]) => String(url).endsWith("/screening/run"));
    expect(JSON.parse(String(call?.[1]?.body)).parameters.dividend_min).toBe(
      0.05,
    );
    expect(screen.getByText("-4.00%")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("tab", { name: "筛选结果" })).toHaveAttribute("aria-selected", "true"));
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
      (screen.getByLabelText("最低股息率") as HTMLInputElement).valueAsNumber,
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
});
