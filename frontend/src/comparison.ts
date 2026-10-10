import type {
  BacktestDefinition,
  BacktestSummary,
  BacktestResults,
  BacktestJob,
} from "./types";

export const strategies = [
  {
    id: "buy_hold",
    label: "买入持有",
    color: "#64748b",
    rule: "开始日收盘产生买入信号，持有至期末",
    defaults: {},
  },
  {
    id: "ma_cross",
    label: "双均线交叉",
    color: "#2563eb",
    rule: "短均线上穿长均线买入，下穿卖出",
    defaults: { short_window: 20, long_window: 60 },
  },
  {
    id: "macd_cross",
    label: "MACD交叉",
    color: "#9333ea",
    rule: "DIF上穿DEA买入，下穿卖出",
    defaults: { fast_window: 12, slow_window: 26, signal_window: 9 },
  },
  {
    id: "rsi_reversal",
    label: "RSI超卖回升",
    color: "#0891b2",
    rule: "RSI上穿下阈值买入，下穿上阈值卖出",
    defaults: { period: 14, lower: 30, upper: 70 },
  },
  {
    id: "donchian_breakout",
    label: "唐奇安突破",
    color: "#d97706",
    rule: "收盘突破此前最高价买入，跌破此前最低价卖出",
    defaults: { entry_window: 20, exit_window: 10 },
  },
  {
    id: "bollinger_reversion",
    label: "布林带回归",
    color: "#db2777",
    rule: "从下轨外回到轨内买入，上穿中轨卖出",
    defaults: { period: 20, deviations: 2 },
  },
] as const;
export type StrategyId = (typeof strategies)[number]["id"];
export interface StrategyConfig {
  id: StrategyId;
  [key: string]: number | StrategyId;
}
export const strategyDefaults: StrategyConfig[] = strategies.map(
  (strategy) => ({ id: strategy.id, ...strategy.defaults }),
);
export const parameterLabels: Record<string, string> = {
  short_window: "短周期",
  long_window: "长周期",
  fast_window: "快周期",
  slow_window: "慢周期",
  signal_window: "信号周期",
  period: "周期",
  lower: "下阈值",
  upper: "上阈值",
  entry_window: "买入窗口",
  exit_window: "卖出窗口",
  deviations: "标准差倍数",
};
export function warmupDays(config: StrategyConfig): number {
  const value = (key: string) => Number(config[key]);
  switch (config.id) {
    case "buy_hold":
      return 0;
    case "ma_cross":
      return value("long_window");
    case "macd_cross":
      return 5 * value("slow_window") + value("signal_window");
    case "rsi_reversal":
      return 5 * value("period") + 1;
    case "donchian_breakout":
      return Math.max(value("entry_window"), value("exit_window"));
    case "bollinger_reversion":
      return value("period");
  }
}
export type CompareDefinition = Omit<
  BacktestDefinition,
  "strategy" | "short_window" | "long_window"
> & { strategies: StrategyConfig[] };
export interface ComparePoint {
  date: string;
  total_assets: number;
  return_rate: number;
  drawdown: number;
}
export interface StrategyResult {
  strategy_id: StrategyId;
  label: string;
  summary: BacktestSummary;
  parameters: StrategyConfig;
  curve: ComparePoint[];
  warnings: string[];
  assumptions: Record<string, string>;
  excess_return: number | null;
}
export interface ComparisonResults {
  mode: "comparison";
  summary: {
    complete: boolean;
    strategy_count: number;
    complete_count: number;
  };
  strategies: StrategyResult[];
  parameters: CompareDefinition;
  data_versions: Record<string, string>;
  fixture_mode: boolean;
  warnings: string[];
}
export interface StrategyDetail extends StrategyResult {
  prices: {
    date: string;
    open: number | null;
    high: number | null;
    low: number | null;
    close: number | null;
  }[];
  trades: BacktestResults["items"];
  items: BacktestResults["items"];
  total: number;
  page: number;
  page_size: number;
}
export type ComparisonJob = Omit<BacktestJob, "payload" | "result"> & {
  payload: CompareDefinition;
  result: ComparisonResults["summary"] | null;
};
export function isComparison(
  value: BacktestResults | ComparisonResults,
): value is ComparisonResults {
  return "mode" in value && value.mode === "comparison";
}
