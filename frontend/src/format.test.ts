import { describe, expect, it } from "vitest";
import {
  formatFactor,
  formatResearchParameters,
  formatMoney,
  formatPercent,
  performanceClass,
  factorClass,
  formatCompactNumber,
} from "./format";

describe("单位与缺失值", () => {
  it("百分数和元正确显示", () => {
    expect(formatFactor("daily_return", -0.03)).toBe("-3.00%");
    expect(formatFactor("close", 10)).toBe("10.00元");
  });
  it("缺失不显示零", () => {
    expect(formatFactor("dividend_yield_ttm", null)).toBe("—");
    expect(formatFactor("volume_ratio_5d", 0.7)).toBe("0.70倍");
  });
});

it("详情大数按绝对值严格超过门槛缩写，保留单位与缺失语义", () => {
  expect(formatCompactNumber(1_000_000, "元")).toBe("1,000,000元");
  expect(formatCompactNumber(1_000_001, "元")).toBe("100万元");
  expect(formatCompactNumber(12_345_678, "元")).toBe("1,235万元");
  expect(formatCompactNumber(1_000_000_000, "元")).toBe("100,000万元");
  expect(formatCompactNumber(1_234_567_890, "元")).toBe("12.3亿元");
  expect(formatCompactNumber(-1_234_567_890, "元")).toBe("-12.3亿元");
  expect(formatCompactNumber(-2_345_678, "股")).toBe("-235万股");
  expect(formatCompactNumber(0, "元")).toBe("0.00元");
  expect(formatCompactNumber(null, "元")).toBe("—");
  expect(formatCompactNumber(NaN, "元")).toBe("—");
  expect(formatCompactNumber(Infinity, "元")).toBe("—");
});

describe("统一金融展示", () => {
  it("金额超过1000按绝对值四舍五入，边界保留小数", () => {
    expect(formatMoney(1234.5)).toBe("1,235元");
    expect(formatMoney(-1234.5)).toBe("-1,235元");
    expect(formatMoney(1000)).toBe("1,000.00元");
    expect(formatMoney(999.995)).toBe("1,000.00元");
    expect(formatFactor("amount_mean_20d", 1234567.89)).toBe("1,234,568元");
  });
  it("收益按符号红绿，中性与未知黑色，百分数保留两位", () => {
    expect(performanceClass(1)).toBe("positive");
    expect(performanceClass(-1)).toBe("negative");
    expect(performanceClass(0)).toBe("neutral");
    expect(performanceClass(null)).toBe("neutral");
    expect(factorClass("close", 100)).toBe("neutral");
    expect(factorClass("dividend_yield_ttm", 0.05)).toBe("positive");
    expect(formatPercent(0.123456)).toBe("12.35%");
  });
});

it("研究参数隐藏嵌套版本且不改写原任务", () => {
  const parameters = {
    stock_code: "000001.SZ",
    data_versions: { daily: "original" },
    nested: { data_versions: { trade_cal: "old" }, period: 20 },
  };
  expect(JSON.parse(formatResearchParameters(parameters))).toEqual({
    stock_code: "000001.SZ",
    nested: { period: 20 },
  });
  expect(parameters.data_versions.daily).toBe("original");
});
