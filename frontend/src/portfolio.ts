import type {
  BacktestDefinition,
  BacktestOptions,
  BacktestSummary,
  Job,
} from "./types";
import type {
  StrategyConfig,
  StrategyDetail,
  ComparePoint,
} from "./comparison";
export const frequencies = {
  none: "关闭",
  weekly: "周度",
  monthly: "月度",
  quarterly: "季度",
  yearly: "年度",
};
export interface PortfolioMember {
  stock_code: string;
  strategy: StrategyConfig;
  weight?: string | null;
}
export type PortfolioDefinition = Omit<
  BacktestDefinition,
  "stock_code" | "strategy" | "short_window" | "long_window"
> & {
  members: PortfolioMember[];
  allocation: "equal" | "custom";
  rebalance: keyof typeof frequencies;
};
export type PortfolioSummary = Omit<
  BacktestSummary,
  "shares" | "valuation_date" | "pending_order"
>;
export interface PortfolioJob extends Omit<Job, "kind" | "payload" | "result"> {
  kind: "portfolio";
  payload: PortfolioDefinition;
  result: PortfolioSummary | null;
}
export interface PortfolioOptions extends Omit<BacktestOptions, "defaults"> {
  defaults: Omit<
    PortfolioDefinition,
    "members" | "start_date" | "end_date" | "data_versions"
  >;
}
export interface PortfolioRow {
  date: string;
  stock_code: string | null;
  event: string;
  signal_date: string | null;
  shares_delta: number;
  shares: number;
  price: number | null;
  amount: number;
  fee: number;
  tax: number;
  cash: number;
  reason: string;
}
export interface PortfolioResults {
  summary: PortfolioSummary;
  curve: ComparePoint[];
  benchmark: {
    summary: PortfolioSummary;
    curve: ComparePoint[];
    warnings: string[];
  };
  items: PortfolioRow[];
  total: number;
  page: number;
  page_size: number;
  warnings: string[];
  fixture_mode: boolean;
  excess_return: number | null;
  assumptions: Record<string, string>;
  parameters: PortfolioDefinition;
  data_versions: Record<string, string>;
}
export interface PortfolioPositions {
  date: string | null;
  cash: number;
  cash_weight: number | null;
  items: {
    stock_code: string;
    name: string;
    strategy: StrategyConfig;
    shares: number;
    price: number | null;
    position_value: number;
    valuation_date: string | null;
    receivable: number;
    tax_liability: number;
    configured_weight: number;
    target_weight: number;
    actual_weight: number | null;
    eligible: boolean;
    pending_order: { signal_date: string; target_value: number } | null;
  }[];
}
export interface PortfolioStockDetail {
  stock_code: string;
  label: string;
  prices: StrategyDetail["prices"];
  trades: PortfolioRow[];
  items: PortfolioRow[];
  total: number;
  warnings: string[];
}
