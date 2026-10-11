import type { StrategyId } from "./comparison";

interface SignalExample {
  before: string;
  after: string;
  signal: string;
}

interface StrategyArticle {
  category: string;
  idea: string;
  calculation: string;
  rules: string[];
  examples: SignalExample[];
  limitation: string;
  reference: { title: string; publisher: string; url: string };
}

const fidelity =
  "https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/";

export const strategyArticles: Record<StrategyId, StrategyArticle> = {
  buy_hold: {
    category: "持有基准",
    idea: "一次买入后持续持有，用来观察同一股票在研究区间内的持有表现。它也是比较其他策略收益时的参考，不主动判断买卖时机。",
    calculation:
      "无需技术指标。收益来自持仓价格变化与可确认的现金分红，账户中未用于买入的余款仍计入现金。",
    rules: [
      "开始日收盘只产生一次买入信号，下一交易日开盘尝试用可用资金买入；遇到市场限制则延期重试。",
      "不追加资金、不主动卖出，也不因分红到账再次加仓。期末持仓按可确认价格估值，不强制清仓。",
    ],
    examples: [
      {
        before: "研究尚未开始，持仓为 0",
        after: "开始日收盘，持仓仍为 0",
        signal: "产生买入信号，下一交易日开盘尝试买入",
      },
      {
        before: "已买入并持有",
        after: "价格上涨或下跌",
        signal: "不产生卖出信号，继续持有",
      },
    ],
    limitation:
      "持续承担个股下跌风险，没有主动退出机制。机构资料中的分散指数长期投资案例不能直接代表本站单只 A 股的持有结果。",
    reference: {
      title: "Time-Tested Investing Advice",
      publisher: "Charles Schwab",
      url: "https://www.schwab.com/index-compound-investing",
    },
  },
  ma_cross: {
    category: "趋势跟随",
    idea: "用短期与长期价格均值的交叉观察趋势变化。短均线更快反映近期价格，长均线更平滑。",
    calculation:
      "简单移动平均线 SMA = 最近 N 个交易日复权收盘价之和 ÷ N，包含信号当天。",
    rules: [
      "前日短均线 ≤ 长均线，当日短均线 > 长均线时产生买入信号；前日短均线 ≥ 长均线，当日短均线 < 长均线时产生卖出信号。",
      "开始日已经处于短均线高于长均线的状态时，不自动建仓，等待新的交叉。相等本身不触发信号，计算中会容忍极小浮点误差。",
    ],
    examples: [
      {
        before: "短均线 9.90，长均线 10.00",
        after: "短均线 10.10，长均线 10.00",
        signal: "金叉，产生买入信号",
      },
      {
        before: "短均线 10.10，长均线 10.00",
        after: "短均线 9.90，长均线 10.00",
        signal: "死叉，产生卖出信号",
      },
    ],
    limitation:
      "均线有滞后性，震荡区间容易反复交叉并增加费用。不同周期会改变信号频率，默认周期不是经本站验证的最优参数。",
    reference: {
      title: "Simple Moving Average (SMA)",
      publisher: "Fidelity",
      url: fidelity + "sma",
    },
  },
  macd_cross: {
    category: "趋势与动量",
    idea: "MACD 比较快慢两条指数移动平均线的差，再与该差值的平滑线比较，观察价格动量的变化。",
    calculation:
      "DIF = EMA(快周期) − EMA(慢周期)；DEA = EMA(DIF, 信号周期)。EMA 以首个完整周期的简单均值初始化，再按 2 ÷ (周期 + 1) 递推；DEA 同样用首个完整周期的 DIF 均值初始化。",
    rules: [
      "前日 DIF ≤ DEA、当日 DIF > DEA 时买入；前日 DIF ≥ DEA、当日 DIF < DEA 时卖出。相等本身不触发信号，交叉容忍极小浮点误差。",
      "本站只使用 DIF 与 DEA 的交叉，不附加零轴、柱状图或背离过滤。开始已有 DIF > DEA 时仍等待新的交叉。",
    ],
    examples: [
      {
        before: "DIF 0.08，DEA 0.10",
        after: "DIF 0.12，DEA 0.11",
        signal: "DIF 上穿 DEA，产生买入信号",
      },
      {
        before: "DIF 0.12，DEA 0.11",
        after: "DIF 0.08，DEA 0.10",
        signal: "DIF 下穿 DEA，产生卖出信号",
      },
    ],
    limitation:
      "递归指标依赖初始化和预热起点，其他软件的数值可能不同。震荡时交叉会反复出现，信号不能保证趋势延续。",
    reference: { title: "MACD", publisher: "Fidelity", url: fidelity + "macd" },
  },
  rsi_reversal: {
    category: "超卖回升",
    idea: "RSI 将近期上涨与下跌幅度转换为 0–100 的指标。本站等待指标从低位回升，或从高位回落后再产生信号。",
    calculation:
      "RSI = 100 − 100 ÷ (1 + 平均涨幅 ÷ 平均跌幅)。本站采用 Wilder 平滑：首个周期取平均涨跌幅，之后用 (前值 × (周期 − 1) + 当日涨跌幅) ÷ 周期递推；全无涨跌为 50，仅上涨为 100，仅下跌为 0。",
    rules: [
      "前日 RSI ≤ 下阈值、当日 RSI > 下阈值时买入；前日 RSI ≥ 上阈值、当日 RSI < 上阈值时卖出。",
      "默认等待 RSI 上穿 30 才买入，下穿 70 才卖出。单纯低于 30 或高于 70 不产生相应信号，恰好等于阈值也不触发；交叉容忍极小浮点误差。",
    ],
    examples: [
      { before: "RSI 28", after: "RSI 32", signal: "上穿 30，产生买入信号" },
      { before: "RSI 73", after: "RSI 68", signal: "下穿 70，产生卖出信号" },
      {
        before: "RSI 25",
        after: "RSI 28",
        signal: "仍低于 30，不产生买入信号",
      },
    ],
    limitation:
      "强趋势中 RSI 可能长期处于低位或高位，超卖回升不保证价格已经见底。过早退出也可能错过后续上涨。",
    reference: {
      title: "Relative Strength Index (RSI)",
      publisher: "Fidelity",
      url: fidelity + "RSI",
    },
  },
  donchian_breakout: {
    category: "价格突破",
    idea: "观察收盘价是否突破此前一段时间的价格边界，尝试跟随新趋势；退出使用独立的较短窗口。",
    calculation:
      "买入边界 = 此前买入窗口内复权最高价的最大值；卖出边界 = 此前卖出窗口内复权最低价的最小值。两个窗口均不含当天，比较的是当天复权收盘价。",
    rules: [
      "当日收盘严格高于此前买入窗口最高价时产生买入信号；严格低于此前卖出窗口最低价时产生卖出信号。等于边界不触发。",
      "本站使用买入与卖出两个不同窗口，默认分别为 20 日与 10 日。突破条件成立即可发出信号，不要求前一天发生交叉；已有持仓不会因重复买入信号加仓。",
    ],
    examples: [
      {
        before: "截至前日，此前 20 日最高价 12.00",
        after: "当日收盘价 12.10",
        signal: "突破买入边界，产生买入信号",
      },
      {
        before: "截至前日，此前 10 日最低价 10.00",
        after: "当日收盘价 9.90",
        signal: "跌破卖出边界，产生卖出信号",
      },
    ],
    limitation:
      "假突破可能带来连续小额亏损；买入边界与卖出边界之间的距离可能较大。外部指标图的窗口定义需核对，本站明确排除当天。",
    reference: {
      title: "Donchian Channels (DC)",
      publisher: "TradingView",
      url: "https://www.tradingview.com/support/solutions/43000502253-donchian-channels-dc/",
    },
  },
  bollinger_reversion: {
    category: "均值回归",
    idea: "用均线和波动范围描述价格相对位置，等待价格从下轨外回到轨内，再观察向均值回归的过程。",
    calculation:
      "中轨 = 最近 N 日复权收盘价的简单均值；上下轨 = 中轨 ± 标准差倍数 × 总体标准差。总体标准差的方差分母为 N；前日与当日各自使用包含当天的滚动窗口。",
    rules: [
      "前日收盘价 ≤ 前日下轨、当日收盘价 > 当日下轨时买入；前日收盘价 ≤ 前日中轨、当日收盘价 > 当日中轨时卖出。",
      "本站卖出条件是上穿中轨，不是触及上轨。同日同时出现买入和卖出信号时卖出优先；相等本身不触发，交叉容忍极小浮点误差。",
    ],
    examples: [
      {
        before: "收盘 9.70，下轨 9.80，中轨 10.00",
        after: "收盘 9.90，下轨 9.85，中轨 10.02",
        signal: "上穿下轨但未上穿中轨，产生买入信号",
      },
      {
        before: "收盘 10.00，中轨 10.05",
        after: "收盘 10.15，中轨 10.08",
        signal: "上穿中轨，产生卖出信号",
      },
    ],
    limitation:
      "下跌趋势中价格可能长期贴着下轨运行，回到轨内不保证反转。轨道也随价格变化，上穿可以来自价格与轨道的共同移动。",
    reference: {
      title: "Bollinger Bands",
      publisher: "Fidelity",
      url: fidelity + "bollinger-bands",
    },
  },
};
