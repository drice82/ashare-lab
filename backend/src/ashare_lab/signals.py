"""Shared close-of-day technical signals, with pinned, bounded market reads."""

from datetime import date, timedelta
from math import isfinite, sqrt
from typing import Any, Protocol

import polars as pl

from ashare_lab.factors import adjusted_prices
from ashare_lab.schemas import StrategyConfig


class SignalRepository(Protocol):
    def read(
        self,
        dataset: str,
        snapshot: dict[str, str],
        codes: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pl.DataFrame: ...

    def coverage(self, dataset: str, snapshot: dict[str, str]) -> dict[str, Any]: ...


def warmup(config: StrategyConfig) -> int:
    return {
        "buy_hold": 0,
        "ma_cross": config.long_window,
        "macd_cross": 5 * config.slow_window + config.signal_window,
        "rsi_reversal": 5 * config.period + 1,
        "donchian_breakout": max(config.entry_window, config.exit_window),
        "bollinger_reversion": config.period,
    }[config.id]


def ema(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    value = sum(values[:period]) / period
    result[period - 1] = value
    for index in range(period, len(values)):
        value += 2 / (period + 1) * (values[index] - value)
        result[index] = value
    return result


def rsi(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return result
    changes = [b - a for a, b in zip(values, values[1:], strict=False)]
    gain = sum(max(v, 0) for v in changes[:period]) / period
    loss = sum(max(-v, 0) for v in changes[:period]) / period
    for index in range(period, len(values)):
        if index > period:
            change = changes[index - 1]
            gain = (gain * (period - 1) + max(change, 0)) / period
            loss = (loss * (period - 1) + max(-change, 0)) / period
        result[index] = (
            50 if gain == loss == 0 else 100 if loss == 0 else 100 - 100 / (1 + gain / loss)
        )
    return result


def crossing(previous: float, current: float, level: float = 0) -> str | None:
    tolerance = max(abs(previous), abs(current), abs(level), 1) * 1e-12
    old = previous - level
    new = current - level
    old = 0 if abs(old) <= tolerance else old
    new = 0 if abs(new) <= tolerance else new
    return "buy" if old <= 0 < new else "sell" if old >= 0 > new else None


def signal(
    config: StrategyConfig, closes: list[float], highs: list[float], lows: list[float]
) -> str | None:
    if config.id == "ma_cross":
        differences = []
        for stop in (len(closes) - 1, len(closes)):
            ma_values = closes[stop - config.long_window : stop]
            short = sum(ma_values[-config.short_window :]) / config.short_window
            long = sum(ma_values) / config.long_window
            difference = short - long
            differences.append(
                0.0 if abs(difference) <= max(abs(short), abs(long)) * 1e-12 else difference
            )
        return crossing(*differences)
    if config.id == "donchian_breakout":
        if closes[-1] > max(highs[-config.entry_window - 1 : -1]):
            return "buy"
        if closes[-1] < min(lows[-config.exit_window - 1 : -1]):
            return "sell"
        return None
    if config.id == "macd_cross":
        fast, slow = ema(closes, config.fast_window), ema(closes, config.slow_window)
        dif = [a - b for a, b in zip(fast, slow, strict=True) if a is not None and b is not None]
        dea = ema(dif, config.signal_window)
        if len(dif) < 2 or dea[-2] is None or dea[-1] is None:
            return None
        return crossing(dif[-2] - dea[-2], dif[-1] - dea[-1])
    if config.id == "rsi_reversal":
        values = rsi(closes, config.period)
        previous, current = values[-2:]
        if previous is None or current is None:
            return None
        if crossing(previous, current, config.upper) == "sell":
            return "sell"
        return "buy" if crossing(previous, current, config.lower) == "buy" else None
    if config.id == "bollinger_reversion":
        differences = []
        middle_differences = []
        for stop in (len(closes) - 1, len(closes)):
            window = closes[stop - config.period : stop]
            middle = sum(window) / config.period
            deviation = sqrt(sum((value - middle) ** 2 for value in window) / config.period)
            differences.append(closes[stop - 1] - (middle - config.deviations * deviation))
            middle_differences.append(closes[stop - 1] - middle)
        if crossing(*middle_differences) == "buy":
            return "sell"
        return "buy" if crossing(*differences) == "buy" else None
    return None


def calculate_signal(
    repository: SignalRepository,
    stock_code: str,
    config: StrategyConfig,
    trade_date: str,
    data_versions: dict[str, str],
    strategy_version: str,
) -> dict[str, Any]:
    """Same initialization window as a backtest starting on this signal date.

    No dividend/limit/order reads: signal evaluation needs no execution simulation.
    MACD starts EMA with SMA, RSI starts Wilder averages at the first full period.
    """
    outcome: dict[str, Any] = {
        "status": "insufficient_data",
        "stock_code": stock_code,
        "direction": None,
        "reason": "数据不足",
        "data_versions": data_versions,
        "strategy_version": strategy_version,
    }
    required = {"stock_basic", "trade_cal", "daily", "adj_factor", "suspend_d"}
    if not required <= data_versions.keys():
        return {**outcome, "reason": "依赖数据版本缺失"}
    instruments = repository.read("stock_basic", data_versions, [stock_code]).to_dicts()
    if not any(
        r.get("list_status") != "D"
        and not r.get("delist_date")
        and r.get("list_date", "") <= trade_date
        for r in instruments
    ):
        return {**outcome, "status": "inactive_instrument", "reason": "证券已退市或信号日未上市"}
    calendar_rows = repository.read("trade_cal", data_versions, end_date=trade_date).to_dicts()
    calendar = {r["cal_date"]: r for r in calendar_rows if r.get("exchange") == "SSE"}
    opened = sorted(day for day, r in calendar.items() if r["is_open"] == 1 and day <= trade_date)
    count = warmup(config) + 1
    days = opened[-count:]
    if len(days) != count or days[-1] != trade_date:
        return {**outcome, "reason": "策略预热交易日不足"}
    cursor = date.fromisoformat(days[0])
    while cursor <= date.fromisoformat(trade_date):
        if cursor.isoformat() not in calendar:
            return {**outcome, "reason": "策略窗口日历不完整"}
        cursor += timedelta(days=1)
    for dataset in ("daily", "adj_factor", "suspend_d"):
        covered = {
            day
            for entry in repository.coverage(dataset, data_versions).values()
            if entry.get("complete")
            for day in (
                [entry["trade_date"]] if entry.get("trade_date") else entry.get("trade_dates", [])
            )
        }
        needed = [trade_date] if dataset == "suspend_d" else days
        if any(day not in covered for day in needed):
            return {**outcome, "reason": f"{dataset} 未确认完整覆盖"}
    bars = {
        r["trade_date"]: r
        for r in repository.read(
            "daily", data_versions, [stock_code], days[0], trade_date
        ).to_dicts()
    }
    factors = {
        r["trade_date"]: r["adj_factor"]
        for r in repository.read(
            "adj_factor", data_versions, [stock_code], days[0], trade_date
        ).to_dicts()
    }
    suspended = repository.read(
        "suspend_d", data_versions, [stock_code], trade_date, trade_date
    ).to_dicts()
    if (
        any(r.get("suspend_type") == "S" for r in suspended)
        or bars.get(trade_date, {}).get("volume") == 0
    ):
        return {**outcome, "status": "checked", "reason": "停牌或零成交量"}
    bar = bars.get(trade_date, {})
    if bar.get("volume") is None or not isfinite(float(bar["volume"])) or float(bar["volume"]) < 0:
        return {**outcome, "reason": "缺少有效行情"}
    fields = ("close", "high", "low") if config.id == "donchian_breakout" else ("close",)
    prices = [
        adjusted_prices(days, bars, factors, factors.get(trade_date), field) for field in fields
    ]
    if any(values is None or any(not isfinite(v) or v <= 0 for v in values) for values in prices):
        return {**outcome, "reason": "策略窗口行情或复权因子缺失"}
    close = prices[0]
    assert close is not None
    high, low = (prices[1], prices[2]) if len(prices) == 3 else (close, close)
    assert high is not None and low is not None
    direction = (
        ma_signal(trade_date, days, bars, factors, config.short_window, config.long_window)[0]
        if config.id == "ma_cross"
        else signal(config, close, high, low)
    )
    reasons = {
        "ma_cross": "短均线与长均线交叉",
        "macd_cross": "DIF与DEA交叉",
        "rsi_reversal": "RSI穿越阈值",
        "bollinger_reversion": "收盘价回归布林轨道或上穿中轨",
        "donchian_breakout": "收盘价突破此前窗口最高价或最低价",
    }
    return {
        **outcome,
        "status": "checked",
        "direction": direction,
        "reason": reasons[config.id] if direction else "未触发",
        "parameters": config.model_dump(),
        "stock_name": instruments[0].get("name", instruments[0].get("stock_name", "")),
        "evidence": {
            "adjusted_close": close[-1],
            "window_start": days[0],
            "warmup_days": count - 1,
            **indicator_evidence(config, close, high, low),
        },
    }


def ma_signal(
    day: str,
    days: list[str],
    bars: dict[str, Any],
    factors: dict[str, Any],
    short_window: int,
    long_window: int,
) -> tuple[str | None, str | None]:
    index = days.index(day)
    if index < long_window:
        return None, "均线预热交易日不足，无法确认交叉"
    directions = []
    for offset in (1, 0):
        stop = index + 1 - offset
        window = days[stop - long_window : stop]
        values = adjusted_prices(
            window,
            bars,
            factors,
            factors.get(days[index - offset]),
        )
        if values is None or any(v <= 0 for v in values):
            return None, "均线窗口行情或复权因子缺失"
        short = sum(values[-short_window:]) / short_window
        long = sum(values) / long_window
        difference = short - long
        directions.append(
            0.0 if abs(difference) <= max(abs(short), abs(long)) * 1e-12 else difference
        )
    if directions[0] <= 0 < directions[1]:
        return "buy", None
    if directions[0] >= 0 > directions[1]:
        return "sell", None
    return None, None


def indicator_evidence(
    config: StrategyConfig, closes: list[float], highs: list[float], lows: list[float]
) -> dict[str, Any]:
    """Numeric values make a notification manually reproducible."""
    if config.id == "ma_cross":
        return {
            "previous_short": sum(closes[-config.short_window - 1 : -1]) / config.short_window,
            "previous_long": sum(closes[-config.long_window - 1 : -1]) / config.long_window,
            "current_short": sum(closes[-config.short_window :]) / config.short_window,
            "current_long": sum(closes[-config.long_window :]) / config.long_window,
        }
    if config.id == "macd_cross":
        fast, slow = ema(closes, config.fast_window), ema(closes, config.slow_window)
        dif = [a - b for a, b in zip(fast, slow, strict=True) if a is not None and b is not None]
        dea = ema(dif, config.signal_window)
        return {
            "previous_dif": dif[-2],
            "current_dif": dif[-1],
            "previous_dea": dea[-2],
            "current_dea": dea[-1],
        }
    if config.id == "rsi_reversal":
        values = rsi(closes, config.period)
        return {
            "previous_rsi": values[-2],
            "current_rsi": values[-1],
            "lower": config.lower,
            "upper": config.upper,
        }
    if config.id == "donchian_breakout":
        return {
            "previous_high": max(highs[-config.entry_window - 1 : -1]),
            "previous_low": min(lows[-config.exit_window - 1 : -1]),
        }
    result = {}
    for label, stop in (("previous", len(closes) - 1), ("current", len(closes))):
        window = closes[stop - config.period : stop]
        middle = sum(window) / config.period
        deviation = sqrt(sum((value - middle) ** 2 for value in window) / config.period)
        result[f"{label}_middle"] = middle
        result[f"{label}_lower"] = middle - config.deviations * deviation
    return result
