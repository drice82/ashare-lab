export type FactorValue = number | boolean | null;
export type Condition = {
  factor: string;
  operator: string;
  value: number | boolean;
  enabled: boolean;
};
export type Group = {
  logic: "AND" | "OR";
  children: (Condition | Group)[];
  enabled?: boolean;
};
export type Parameters = Record<string, number | boolean | Group>;
export interface Rule {
  id: string;
  name: string;
  defaults: Parameters;
  version: string;
  schema: {
    properties: Record<
      string,
      { minimum?: number; maximum?: number; type?: string }
    >;
  };
}
export interface Factor {
  id: string;
  name: string;
  unit: string;
}
export interface ScreeningDefinition {
  as_of_date: string;
  mode?: "rule" | "signal" | "rule_signal" | "dividend";
  rule_id: string | null;
  universe: { type: "all" | "custom"; codes: string[] };
  parameters: Parameters;
  dividend_filter?: { days: number } | null;
  signal_filter?: {
    strategies: import("./comparison").StrategyConfig[];
    direction: "buy" | "sell" | "either";
  } | null;
}
interface JobBase {
  snapshot?: Record<string, string>;
  id: string;
  status: string;
  progress: number;
  created_at: string;
  heartbeat: string | null;
  error: string | null;
  result: {
    matched: number;
    total: number;
    insufficient_data: number;
    fixture_mode?: boolean;
    warnings?: string[];
  } | null;
  logs?: { at: string; level: string; message: string }[];
}
export type ScreeningJob = JobBase & {
  kind: "screening";
  payload: ScreeningDefinition;
};
export type Job =
  | ScreeningJob
  | (JobBase & {
      kind: "sync";
      payload: {
        mode?: "initialize" | "incremental" | "namechange" | "dividend";
      };
    });
export interface Dataset {
  dataset: string;
  version_id: string;
  source: string;
  updated_at: string;
  rows: number;
  stock_count: number;
  start_date: string | null;
  end_date: string | null;
  coverage: Record<
    string,
    { complete: boolean; rows: number; trade_date?: string }
  >;
  partitions: number;
}
export interface MarketStatus {
  incremental_schedule?: {
    enabled: boolean;
    schedule: string;
    timezone: string;
    next_run: string;
    last_completed: string | null;
    latest_job: { id: string; status: string } | null;
    limitation: string;
    last_full_coverage?: string | null;
  };
  dividend_schedule?: {
    enabled: boolean;
    schedule: string;
    timezone: string;
    next_run: string;
    last_completed: string | null;
    latest_job: { id: string; status: string } | null;
    limitation: string;
    last_full_coverage?: string | null;
  };
  namechange_schedule?: {
    enabled: boolean;
    schedule: string;
    timezone: string;
    next_run: string;
    last_completed: string | null;
    latest_job: { id: string; status: string } | null;
    limitation: string;
    last_full_coverage?: string | null;
  };
  dividend_full_schedule?: MarketStatus["dividend_schedule"];
  namechange_full_schedule?: MarketStatus["namechange_schedule"];
  universe_policy?: string;
  latest_market_date: string | null;
  stock_count: number;
  datasets: Dataset[];
  missing_trade_dates: string[];
  token_configured: boolean;
  fixture_mode: boolean;
  recent_screening: ScreeningJob[];
  recent_sync: Job[];
  latest_sync_status?: string | null;
  pit_limitation: string;
}
export interface Preset {
  id: string;
  name: string;
  definition: ScreeningDefinition;
}
export interface ScreeningRow {
  stock_code: string;
  stock_name: string;
  as_of_date: string;
  latest_trade_date: string | null;
  rule_id: string | null;
  match_status: string;
  factor_values: Record<string, FactorValue>;
  data_quality_flags: string[];
  condition_results: {
    factor: string;
    operator: string;
    threshold: number | boolean;
    actual: FactorValue;
    matched: boolean | null;
  }[];
  signal_results?: SignalResult[];
  recent_dividend?: RecentDividend;
}
export interface SignalResult {
  strategy_id: import("./comparison").StrategyId;
  parameters: import("./comparison").StrategyConfig;
  status: "checked" | "insufficient_data" | "inactive_instrument";
  direction: "buy" | "sell" | null;
  trade_date: string;
  matched: boolean | null;
  reason: string;
  evidence?: Record<string, number | string>;
}
export interface Results {
  items: ScreeningRow[];
  total: number;
  page: number;
  page_size: number;
  summary: {
    matched: number;
    total: number;
    insufficient_data: number;
    signal_version?: string;
    as_of_date?: string;
    effective_market_dates?: string[];
    missing_market_dates?: number;
  };
}
export interface Bar {
  trade_date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
  ma20: number | null;
  ma60: number | null;
}
export interface BarsResponse {
  items: Bar[];
  adjustment: string;
  anchor_date: string;
  instrument: {
    name: string;
    stock_code: string;
    industry: string | null;
    list_date: string;
    exchange: string;
  } | null;
}

export interface DcaDefinition {
  stock_code: string;
  start_date: string;
  end_date: string;
  monthly_amount: string;
  monthly_day: number;
  commission_rate: string;
  minimum_commission: string;
  transfer_rate: string;
  data_versions: Record<string, string>;
}
export interface DcaSummary {
  complete: boolean;
  confirmed_through: string | null;
  contributed: number;
  cash: number;
  shares: number;
  position_value: number;
  receivable: number;
  dividends: number;
  fees: number;
  dividend_tax_paid: number;
  total_assets: number | null;
  profit: number | null;
  return_rate: number | null;
  annualized_return?: number | null;
  max_drawdown?: number | null;
  valuation_date: string | null;
  pending_plans: number;
}
export interface DcaJob extends Omit<Job, "kind" | "payload" | "result"> {
  kind: "dca";
  payload: DcaDefinition;
  result: (DcaSummary & { warnings: string[] }) | null;
}
export interface DcaOptions {
  calendar_start?: string | null;
  trading_days?: string[];
  data_versions: Record<string, string>;
  latest_market_date: string | null;
  fixture_mode: boolean;
  defaults: Pick<
    DcaDefinition,
    | "monthly_amount"
    | "monthly_day"
    | "commission_rate"
    | "minimum_commission"
    | "transfer_rate"
  >;
}
export interface DcaPoint {
  date: string;
  total_assets: number;
  contributed: number;
  valuation_date: string | null;
}
export interface DcaRow {
  date: string;
  event: string;
  planned_dates: string[];
  shares_delta: number;
  shares: number;
  price: number | null;
  amount: number;
  fee: number;
  cash: number;
  reason: string;
}
export interface DcaResults {
  summary: DcaSummary;
  curve: DcaPoint[];
  items: DcaRow[];
  total: number;
  page: number;
  page_size: number;
  warnings: string[];
  fixture_mode: boolean;
  data_versions: Record<string, string>;
  parameters: DcaDefinition;
  assumptions: Record<string, string>;
}
export interface BacktestDefinition extends Omit<
  DcaDefinition,
  "monthly_amount" | "monthly_day"
> {
  initial_cash: string;
  strategy: "buy_hold" | "ma_cross";
  short_window: number;
  long_window: number;
  stamp_rate: string;
}
export interface BacktestSummary extends Omit<DcaSummary, "pending_plans"> {
  tax_liability: number;
  max_drawdown: number | null;
  pending_orders: number;
  pending_order: { side: "buy" | "sell"; signal_date: string } | null;
  buy_count: number;
  sell_count: number;
}
export interface BacktestJob extends Omit<Job, "kind" | "payload" | "result"> {
  kind: "backtest";
  payload: BacktestDefinition;
  result: (BacktestSummary & { warnings: string[] }) | null;
}
export interface BacktestOptions extends Omit<DcaOptions, "defaults"> {
  comparison_defaults?: { strategies: import("./comparison").StrategyConfig[] };
  defaults: Omit<
    BacktestDefinition,
    "stock_code" | "start_date" | "end_date" | "data_versions"
  >;
}
export interface BacktestResults extends Omit<
  DcaResults,
  "summary" | "items" | "parameters"
> {
  summary: BacktestSummary;
  items: (DcaRow & { signal_date: string | null; tax: number })[];
  parameters: BacktestDefinition;
}
export type ResearchJob =
  | Job
  | DcaJob
  | BacktestJob
  | import("./comparison").ComparisonJob
  | import("./portfolio").PortfolioJob;

export interface DividendEvent {
  end_date: string | null;
  ann_date: string | null;
  imp_ann_date: string | null;
  record_date: string;
  ex_date: string | null;
  pay_date: string | null;
  cash_per_share: number;
  cash_per_10: number;
  estimated_cash_10000: number | null;
}
export interface RecentDividend {
  matched: boolean | null;
  days: number;
  events: DividendEvent[];
  flags: string[];
}
export interface CompanySection {
  status: string;
  items: Record<string, string | number | null>[];
  source: string;
  fetched_at: string;
  message?: string;
}
export interface CompanyProfile {
  stock_code: string;
  as_of_date: string | null;
  data_version: string | null;
  sections: Record<string, CompanySection>;
  valuation?: Record<string, string | number | null>;
  fixture_mode: boolean;
  stale: boolean;
  job: { id: string; status: string; progress: number } | null;
}
