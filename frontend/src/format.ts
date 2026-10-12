import type { FactorValue } from "./types";
export const percentFactors = new Set([
  "dividend_yield_ttm",
  "daily_return",
  "return_20d",
  "return_60d",
  "price_distance_ma20",
  "abs_price_distance_ma20",
  "drawdown_60d",
  "ma20_change_5d",
]);
export const performanceColors = {
  positive: "#bd4940",
  negative: "#258568",
  neutral: "#000000",
};
export function performanceClass(
  value: number | null | undefined,
): "positive" | "negative" | "neutral" {
  return value != null && Number.isFinite(value) && value !== 0
    ? value > 0
      ? "positive"
      : "negative"
    : "neutral";
}
export function performanceColor(value: number | null | undefined): string {
  return performanceColors[performanceClass(value)];
}
export function formatMoney(
  value: number | null | undefined,
  suffix = "元",
  missing = "—",
): string {
  if (value == null || !Number.isFinite(value)) return missing;
  const digits = Math.abs(value) > 1000 ? 0 : 2;
  return `${value.toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits })}${suffix}`;
}
export function formatPercent(
  value: number | null | undefined,
  missing = "—",
): string {
  return value == null || !Number.isFinite(value)
    ? missing
    : `${(value * 100).toFixed(2)}%`;
}

/** Base-unit values; thresholds also apply to negative amounts by magnitude. */
export function formatCompactNumber(
  value: number | null | undefined,
  suffix = "",
): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const magnitude = Math.abs(value);
  if (magnitude > 1e9) return `${(value / 1e8).toFixed(1)}亿${suffix}`;
  if (magnitude > 1e6)
    return `${(value / 1e4).toLocaleString("zh-CN", { maximumFractionDigits: 0 })}万${suffix}`;
  if (suffix === "元") return formatMoney(value);
  return `${value.toLocaleString("zh-CN")}${suffix}`;
}
const signedFactors = new Set([
  "daily_return",
  "return_20d",
  "return_60d",
  "dividend_yield_ttm",
  "ma20_change_5d",
]);
export function factorClass(
  key: string,
  value: FactorValue | undefined,
): string {
  if (typeof value !== "number") return "neutral";
  if (key.startsWith("drawdown_")) return performanceClass(-Math.abs(value));
  return signedFactors.has(key) ? performanceClass(value) : "neutral";
}
export function formatFactor(
  key: string,
  value: FactorValue | undefined,
  compact = false,
): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (percentFactors.has(key)) return formatPercent(value);
  if (key.includes("volume_ratio") || key.endsWith("_ratio"))
    return `${value.toFixed(2)}倍`;
  if (key === "amount_mean_20d")
    return compact ? formatCompactNumber(value, "元") : formatMoney(value);
  if (key === "volume")
    return compact
      ? formatCompactNumber(value, "股")
      : `${value.toLocaleString("zh-CN")}股`;
  if (key === "valid_days_20d") return `${value}日`;
  return formatMoney(value);
}
export const statusLabels: Record<string, string> = {
  queued: "等待执行",
  running: "执行中",
  cancel_requested: "取消中",
  cancelled: "已取消",
  succeeded: "已完成",
  partial: "部分完成",
  failed: "失败",
  matched: "命中",
  not_matched: "未命中",
  insufficient_data: "数据不足",
};
export const activeJob = (status: string) =>
  ["queued", "running", "cancel_requested"].includes(status);

/** Research UI omits internal dataset identifiers, including nested job parameters. */
export function formatResearchParameters(parameters: unknown): string {
  return JSON.stringify(
    parameters,
    (key, value: unknown) => (key === "data_versions" ? undefined : value),
    2,
  );
}
