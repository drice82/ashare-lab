"""Bounded batch reads for the existing single-stock signal algorithm."""

from collections.abc import Callable
from typing import Any

import polars as pl

from ashare_lab.backtest import STRATEGY_VERSION
from ashare_lab.comparison import COMPARISON_VERSION, LABELS, PARAMETERS
from ashare_lab.schemas import STRATEGY_FIELDS, SignalFilter, StrategyConfig
from ashare_lab.signals import calculate_signal, warmup
from ashare_lab.warehouse import MarketRepository

SIGNAL_SCREENING_VERSION = "1.0.0"


def strategy_version(config: StrategyConfig) -> str:
    return STRATEGY_VERSION if config.id == "ma_cross" else COMPARISON_VERSION


def describe_strategies() -> list[dict[str, Any]]:
    return [
        {
            "id": key,
            "name": LABELS[key],
            "version": strategy_version(config),
            "defaults": config.model_dump(),
            "parameters": {field: PARAMETERS[field] for field in STRATEGY_FIELDS[key]},
        }
        for key in LABELS
        if key != "buy_hold"
        for config in [StrategyConfig.model_validate({"id": key})]
    ]


class SignalReadContext:
    def __init__(
        self,
        repository: MarketRepository,
        snapshot: dict[str, str],
        day: str,
        configs: list[StrategyConfig],
    ):
        self.calendar = repository.read("trade_cal", snapshot, end_date=day)
        self.days = sorted(
            {
                row["cal_date"]
                for row in self.calendar.to_dicts()
                if row.get("exchange") == "SSE" and row["is_open"] == 1
            }
        )
        count = max(warmup(config) + 1 for config in configs)
        self.days = self.days[-count:]
        if self.days:
            self.calendar = self.calendar.filter(pl.col("cal_date") >= self.days[0])
        needed = set(self.days)
        self.covered: dict[str, dict[str, Any]] = {}
        for dataset in ("daily", "adj_factor", "suspend_d"):
            dates = {
                covered_day
                for entry in repository.coverage(dataset, snapshot).values()
                if entry.get("complete")
                for covered_day in (
                    [entry["trade_date"]]
                    if entry.get("trade_date")
                    else entry.get("trade_dates", [])
                )
                if covered_day in ({day} if dataset == "suspend_d" else needed)
            }
            self.covered[dataset] = {"window": {"complete": True, "trade_dates": sorted(dates)}}


class BatchSignalRepository:
    """In-memory facade: algorithm checks still apply to each strategy window."""

    def __init__(
        self,
        repository: MarketRepository,
        snapshot: dict[str, str],
        day: str,
        instruments: list[dict[str, Any]],
        configs: list[StrategyConfig],
        context: SignalReadContext,
    ):
        self.context = context
        codes = [row["stock_code"] for row in instruments]
        count = max(warmup(config) + 1 for config in configs)
        days = context.days[-count:]
        start = days[0] if days else day
        self.frames: dict[str, dict[str, pl.DataFrame]] = {
            "stock_basic": {row["stock_code"]: pl.DataFrame([row]) for row in instruments},
        }
        self.empty: dict[str, pl.DataFrame] = {}
        for dataset in ("daily", "adj_factor", "suspend_d"):
            frame = repository.read(
                dataset, snapshot, codes, day if dataset == "suspend_d" else start, day
            )
            self.empty[dataset] = frame.clear()
            self.frames[dataset] = (
                {
                    str(key[0]): part
                    for key, part in frame.partition_by("stock_code", as_dict=True).items()
                }
                if "stock_code" in frame.columns
                else {}
            )

    def read(
        self,
        dataset: str,
        snapshot: dict[str, str],
        codes: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pl.DataFrame:
        if dataset == "trade_cal":
            return self.context.calendar
        assert codes is not None and len(codes) == 1
        frame = self.frames[dataset].get(codes[0], self.empty.get(dataset, pl.DataFrame()))
        if "trade_date" in frame.columns:
            if start_date:
                frame = frame.filter(pl.col("trade_date") >= start_date)
            if end_date:
                frame = frame.filter(pl.col("trade_date") <= end_date)
        return frame

    def coverage(self, dataset: str, snapshot: dict[str, str]) -> dict[str, Any]:
        return self.context.covered[dataset]


def batch_signals(
    repository: MarketRepository,
    instruments: list[dict[str, Any]],
    selection: SignalFilter,
    day: str,
    snapshot: dict[str, str],
    context: SignalReadContext,
    check_cancelled: Callable[[], None] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    cached = BatchSignalRepository(
        repository, snapshot, day, instruments, selection.strategies, context
    )
    outcomes: dict[str, list[dict[str, Any]]] = {}
    for instrument in instruments:
        code = instrument["stock_code"]
        outcomes[code] = []
        for config in selection.strategies:
            if check_cancelled:
                check_cancelled()
            outcome = calculate_signal(
                cached, code, config, day, snapshot, strategy_version(config)
            )
            matched = (
                None
                if outcome["status"] == "insufficient_data"
                else outcome["direction"] is not None
                and (selection.direction == "either" or outcome["direction"] == selection.direction)
            )
            outcomes[code].append(
                {
                    **outcome,
                    "strategy_id": config.id,
                    "parameters": config.model_dump(),
                    "trade_date": day,
                    "matched": matched,
                }
            )
    return outcomes


def any_signal(results: list[dict[str, Any]]) -> bool | None:
    answers = [result["matched"] for result in results]
    return True if True in answers else None if None in answers else False
