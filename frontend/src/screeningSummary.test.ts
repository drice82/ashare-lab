import { expect, it } from "vitest";
import { screeningSummary } from "./screeningSummary";
import type { ScreeningDefinition } from "./types";

it("规则名称说明含义，条件使用保存的实际值而非默认值", () => {
  const saved: ScreeningDefinition = {
    as_of_date: "2024-06-28",
    rule_id: "A",
    universe: { type: "all", codes: [] },
    parameters: {
      dividend_min: 0.035,
      daily_return_max: -0.05,
      exclude_st: true,
      exclude_suspended: false,
    },
  };
  const summary = screeningSummary(saved);
  expect(summary.title).toBe("规则 A：高股息 + 单日下跌");
  expect(summary.conditions).toContain("股息率 ≥ 3.50%");
  expect(summary.conditions).toContain("当日涨跌幅 ≤ -5.00%");
  expect(summary.conditions).toContain("排除 ST");
  expect(summary.conditions).toContain("不排除停牌");
  expect(screeningSummary({ ...saved, parameters: {} }).conditions).toContain(
    "未知",
  );
  expect(screeningSummary(saved, "unknown-version").title).toContain(
    "定义未知",
  );
});

it("多策略名称、买卖方向和参数来自任务", () => {
  const summary = screeningSummary({
    mode: "signal",
    as_of_date: "2024-06-28",
    rule_id: null,
    universe: { type: "all", codes: [] },
    parameters: { exclude_st: false, exclude_suspended: true },
    signal_filter: {
      direction: "either",
      strategies: [
        { id: "ma_cross", short_window: 2, long_window: 5 },
        {
          id: "macd_cross",
          fast_window: 12,
          slow_window: 26,
          signal_window: 9,
        },
      ],
    },
  });
  expect(summary.title).toContain("双均线交叉 / MACD交叉");
  expect(summary.title).toContain("买卖任一");
  expect(summary.conditions).toContain("任一策略触发");
  expect(summary.conditions).toContain("短周期 2");
  expect(summary.conditions).toContain("长周期 5");
});

it("组合条件保留嵌套逻辑并不展示禁用项", () => {
  const summary = screeningSummary({
    rule_id: "F",
    as_of_date: "2024-06-28",
    universe: { type: "all", codes: [] },
    parameters: {
      group: {
        logic: "AND",
        children: [
          { factor: "daily_return", operator: "gte", value: 0, enabled: true },
          {
            logic: "OR",
            children: [
              {
                factor: "volume_ratio_5d",
                operator: "gt",
                value: 1.5,
                enabled: true,
              },
              { factor: "close", operator: "lt", value: 10, enabled: true },
            ],
          },
          { factor: "return_20d", operator: "gt", value: 0.5, enabled: false },
        ],
      },
    },
  });
  expect(summary.conditions).toContain("且");
  expect(summary.conditions).toContain("或");
  expect(summary.conditions).toContain("1.50倍");
  expect(summary.conditions).not.toContain("50.00%");
});
