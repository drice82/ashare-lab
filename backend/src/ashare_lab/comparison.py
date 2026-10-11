"""Pinned independent strategy accounts; no provider calls or current-head lookups."""

from collections.abc import Callable
from datetime import date, timedelta
from math import isfinite
from typing import Any

from ashare_lab.backtest import BACKTEST_VERSION, STRATEGY_VERSION, simulate
from ashare_lab.dca import prepare as prepare_market
from ashare_lab.factors import adjusted_prices
from ashare_lab.schemas import BacktestCompareRequest, BacktestRequest, StrategyConfig
from ashare_lab.signals import ema as ema
from ashare_lab.signals import rsi as rsi
from ashare_lab.signals import signal, warmup
from ashare_lab.warehouse import MarketRepository

COMPARISON_VERSION = "1.0.0"
LABELS = {
    "buy_hold": "买入持有",
    "ma_cross": "双均线交叉",
    "macd_cross": "MACD交叉",
    "rsi_reversal": "RSI超卖回升",
    "donchian_breakout": "唐奇安突破",
    "bollinger_reversion": "布林带回归",
}

PARAMETERS = {
    "short_window": ("短周期", 2, 249, 1),
    "long_window": ("长周期", 3, 250, 1),
    "fast_window": ("快周期", 2, 250, 1),
    "slow_window": ("慢周期", 2, 250, 1),
    "signal_window": ("信号周期", 2, 250, 1),
    "period": ("周期", 2, 250, 1),
    "lower": ("下阈值", 0, 100, 0.1),
    "upper": ("上阈值", 0, 100, 0.1),
    "entry_window": ("买入窗口", 2, 250, 1),
    "exit_window": ("卖出窗口", 2, 250, 1),
    "deviations": ("标准差倍数", 0.5, 5, 0.1),
}


def prepare_comparison(
    request: BacktestCompareRequest, repository: MarketRepository
) -> dict[str, Any]:
    market = prepare_market(request, repository)
    calendar_rows = repository.read(
        "trade_cal", request.data_versions, end_date=request.end_date.isoformat()
    ).to_dicts()
    calendar = {row["cal_date"]: row for row in calendar_rows if row.get("exchange") == "SSE"}
    before = sorted(
        day
        for day, row in calendar.items()
        if row["is_open"] == 1 and day < request.start_date.isoformat()
    )
    count = max(warmup(item) for item in request.strategies)
    pre = before[-count:] if count else []
    market["days"] = pre + market["days"]
    covered: dict[str, set[str]] = {}
    for dataset, key, column in [("daily", "bars", None), ("adj_factor", "factors", "adj_factor")]:
        if pre:
            rows = repository.read(
                dataset, request.data_versions, [request.stock_code], pre[0], pre[-1]
            ).to_dicts()
            market[key].update({row["trade_date"]: row[column] if column else row for row in rows})
        covered[dataset] = {
            day
            for entry in repository.coverage(dataset, request.data_versions).values()
            if entry.get("complete")
            for day in (
                [entry["trade_date"]] if entry.get("trade_date") else entry.get("trade_dates", [])
            )
        }
    issues = {}
    for config in request.strategies:
        needed = warmup(config)
        selected = before[-needed:] if needed else []
        if needed and len(selected) < needed:
            issues[config.id] = "策略预热交易日不足"
        elif selected:
            cursor = date.fromisoformat(selected[0])
            while cursor < request.start_date:
                if cursor.isoformat() not in calendar:
                    issues[config.id] = "策略预热日历不完整"
                    break
                cursor += timedelta(days=1)
            for dataset, coverage in covered.items():
                if any(day not in coverage for day in selected):
                    issues[config.id] = f"{dataset} 策略预热未确认完整发布覆盖"
    market["warmup_issues"] = issues
    return market


def simulate_comparison(
    request: BacktestCompareRequest,
    market: dict[str, Any],
    check: Callable[[], None] = lambda: None,
    progress: Callable[[float], None] = lambda value: None,
) -> dict[str, Any]:
    results = []
    start = request.start_date.isoformat()
    base = request.model_dump(exclude={"strategies"})
    for index, config in enumerate(request.strategies):
        check()
        needed = warmup(config)
        pre = [day for day in market["days"] if day < start]
        days = (pre[-needed:] if needed else []) + [day for day in market["days"] if day >= start]
        local = {**market, "days": days, "issues": dict(market["issues"])}
        problem = market.get("warmup_issues", {}).get(config.id)
        if needed and len(pre) < needed:
            problem = "策略预热交易日不足"
        if problem:
            local["issues"].setdefault(start, problem)
        legacy = BacktestRequest.model_validate(
            {
                **base,
                "strategy": config.id if config.id in {"buy_hold", "ma_cross"} else "buy_hold",
                "short_window": config.short_window,
                "long_window": config.long_window,
            }
        )

        def signal_at(
            day: str,
            local: dict[str, Any] = local,
            days: list[str] = days,
            config: StrategyConfig = config,
        ) -> tuple[str | None, str | None]:
            if local["issues"].get(day):
                return None, local["issues"][day]
            stop = days.index(day) + 1
            window = days[:stop]
            fields = ["close", "high", "low"] if config.id == "donchian_breakout" else ["close"]
            prices = [
                adjusted_prices(
                    window, market["bars"], market["factors"], market["factors"].get(day), field
                )
                for field in fields
            ]
            if any(
                values is None or any(not isfinite(v) or v <= 0 for v in values)
                for values in prices
            ):
                return None, "策略窗口行情或复权因子缺失"
            close = prices[0]
            assert close is not None
            high, low = (prices[1], prices[2]) if len(prices) == 3 else (close, close)
            assert high is not None and low is not None
            return signal(config, close, high, low), None

        def report(value: float, strategy_index: int = index) -> None:
            progress((strategy_index + value) / len(request.strategies))

        output = simulate(
            legacy,
            local,
            check,
            report,
            signal_at if config.id not in {"buy_hold", "ma_cross"} else None,
            LABELS[config.id],
        )
        peak = float(request.initial_cash)
        for point in output["curve"]:
            peak = max(peak, point["total_assets"])
            point["return_rate"] = point["total_assets"] / float(request.initial_cash) - 1
            point["drawdown"] = point["total_assets"] / peak - 1
        results.append(
            {
                **output,
                "strategy_id": config.id,
                "label": LABELS[config.id],
                "parameters": config.model_dump(),
                "strategy_version": STRATEGY_VERSION
                if config.id in {"buy_hold", "ma_cross"}
                else COMPARISON_VERSION,
            }
        )
    baseline = next(
        (
            item
            for item in results
            if item["strategy_id"] == "buy_hold" and item["summary"]["complete"]
        ),
        None,
    )
    for item in results:
        item["excess_return"] = (
            item["summary"]["return_rate"] - baseline["summary"]["return_rate"]
            if baseline and item["summary"]["complete"]
            else None
        )
    # Keep only actual published bars. No synthetic prices for missing/suspended days.
    prices = [
        {"date": day, **{field: bar.get(field) for field in ("open", "high", "low", "close")}}
        for day in days
        if (bar := market["bars"].get(day)) and day >= start and day <= request.end_date.isoformat()
    ]
    return {
        "mode": "comparison",
        "summary": {
            "complete": all(item["summary"]["complete"] for item in results),
            "strategy_count": len(results),
            "complete_count": sum(item["summary"]["complete"] for item in results),
        },
        "strategies": results,
        "prices": prices,
        "warnings": [
            f"{item['label']}：{warning}" for item in results for warning in item["warnings"]
        ],
        "engine_version": BACKTEST_VERSION,
        "comparison_version": COMPARISON_VERSION,
    }
