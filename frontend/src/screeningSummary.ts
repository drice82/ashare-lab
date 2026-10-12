import { parameterLabels, strategies } from "./comparison";
import { formatFactor, formatPercent } from "./format";
import type { Condition, Group, ScreeningDefinition } from "./types";

// Describe the archived 1.0.0 definition, never today's parameter defaults.
const ruleNames: Record<string, string> = {
  A: "高股息 + 单日下跌",
  B: "高股息 + 阶段回调",
  C: "高股息 + 中短期上涨",
  D: "缩量接近20日均线",
  E: "放量突破",
  F: "通用组合条件",
};
const factorNames: Record<string, string> = {
  dividend_yield_ttm: "现金股息率 TTM",
  daily_return: "当日涨跌幅",
  return_20d: "20日收益率",
  return_60d: "60日收益率",
  ma20: "MA20",
  ma60: "MA60",
  close: "未复权收盘价",
  signal_close: "信号收盘价",
  volume: "成交量",
  volume_ratio_5d: "前5日量比",
  volume_ratio_20d: "前20日量比",
  price_distance_ma20: "相对MA20偏离",
  drawdown_60d: "60日回撤",
  ma20_change_5d: "MA20五日变化",
  amount_mean_20d: "20日平均成交额",
  price_ma20_ratio: "收盘价/MA20",
  breakout_price_ratio: "收盘价/前20日最高",
  abs_price_distance_ma20: "MA20绝对偏离",
  valid_days_20d: "20日有效数量",
  breakout_20d: "突破前20日最高",
  is_st: "ST状态",
  is_suspended: "停牌状态",
};
const operators: Record<string, string> = {
  gt: ">",
  gte: "≥",
  lt: "<",
  lte: "≤",
  eq: "=",
};
export const directionLabels = {
  buy: "买入",
  sell: "卖出",
  either: "买卖任一",
};
export function strategyName(id: string): string {
  return strategies.find((strategy) => strategy.id === id)?.label ?? id;
}
function groupDescription(node: Group | Condition): string {
  if (node.enabled === false) return "";
  if ("children" in node) {
    return `(${node.children
      .map(groupDescription)
      .filter(Boolean)
      .join(node.logic === "AND" ? " 且 " : " 或 ")})`;
  }
  return `${factorNames[node.factor] ?? node.factor} ${operators[node.operator] ?? node.operator} ${formatFactor(node.factor, node.value)}`;
}

export function screeningSummary(
  definition: ScreeningDefinition,
  ruleVersion = "1.0.0",
): { title: string; conditions: string } {
  const mode = definition.mode ?? "rule";
  const p = definition.parameters ?? {};
  const number = (key: string) =>
    typeof p[key] === "number" ? (p[key] as number) : null;
  const percent = (key: string) => formatPercent(number(key), "未知");
  const value = (key: string, unit = "") =>
    number(key) == null
      ? "未知"
      : `${number(key)!.toLocaleString("zh-CN")}${unit}`;
  let conditions: string[] = [];
  let title = "";
  if (mode === "rule" || mode === "rule_signal") {
    const id = definition.rule_id ?? "未知";
    const known = ruleVersion === "1.0.0" && ruleNames[id];
    title = `规则 ${id}：${known ? ruleNames[id] : "定义未知"}`;
    if (!known) conditions = ["规则定义或版本未知"];
    else
      switch (id) {
        case "A":
          conditions = [
            `股息率 ≥ ${percent("dividend_min")}`,
            `当日涨跌幅 ≤ ${percent("daily_return_max")}`,
          ];
          break;
        case "B":
          conditions = [
            `股息率 ≥ ${percent("dividend_min")}`,
            `60日回撤 ≥ ${percent("drawdown_min")}`,
            `20日平均成交额 ≥ ${value("amount_min", "元")}`,
          ];
          break;
        case "C":
          conditions = [
            `股息率 ≥ ${percent("dividend_min")}`,
            `20日收益率 ≥ ${percent("return_20d_min")}`,
            `60日收益率 ≥ ${percent("return_60d_min")}`,
            `收盘价/MA20 > ${value("price_ma_min", "倍")}`,
          ];
          break;
        case "D":
          conditions = [
            `前5日量比 ≤ ${value("volume_ratio_max", "倍")}`,
            `MA20绝对偏离 ≤ ${percent("distance_max")}`,
            `MA20五日变化 > ${percent("ma_growth_min")}`,
            "20日窗口完整",
          ];
          break;
        case "E":
          conditions = [
            `收盘价/前20日最高 > ${value("breakout_ratio_min", "倍")}`,
            `前20日量比 ≥ ${value("volume_ratio_min", "倍")}`,
            `收盘价/MA20 > ${value("price_ma_min", "倍")}`,
          ];
          break;
        case "F":
          conditions = [
            p.group && typeof p.group === "object"
              ? groupDescription(p.group)
              : "组合条件未知",
          ];
          break;
      }
  }
  if (mode === "signal" || mode === "rule_signal") {
    const selection = definition.signal_filter;
    const configs = selection?.strategies ?? [];
    const signalTitle = `策略信号：${configs.length ? configs.map((item) => strategyName(item.id)).join(" / ") : "策略未知"} · ${selection ? directionLabels[selection.direction] : "方向未知"}`;
    title = title ? `${title} + ${signalTitle}` : signalTitle;
    if (mode === "rule_signal") conditions.push("规则与信号同时满足");
    conditions.push(
      "任一策略触发",
      ...configs.map(
        (config) =>
          `${strategyName(config.id)}：${Object.entries(config)
            .filter(([key]) => key !== "id")
            .map(([key, val]) => `${parameterLabels[key] ?? key} ${val}`)
            .join(" / ")}`,
      ),
    );
  }
  if (mode === "dividend" || definition.dividend_filter) {
    const days = definition.dividend_filter?.days;
    const description = `未来${days ?? "未知"}天登记、已公开实施公告的现金分红`;
    title = title ? `${title} + 近期分红` : "近期分红筛选";
    conditions.push(description);
  }
  for (const [key, label] of [
    ["exclude_st", "ST"],
    ["exclude_suspended", "停牌"],
  ]) {
    conditions.push(
      typeof p[key] === "boolean"
        ? `${p[key] ? "排除" : "不排除"}${label === "ST" ? " " : ""}${label}`
        : `${label}排除设置未知`,
    );
  }
  return { title, conditions: conditions.join("；") };
}
