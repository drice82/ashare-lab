from unittest.mock import patch

import polars as pl
import pytest
from ashare_lab.factors import FactorEngine, UniverseManager
from ashare_lab.fixtures import FIXTURE_DATE
from ashare_lab.providers import ProviderError, normalize
from ashare_lab.schemas import ScreeningRequest, Universe
from ashare_lab.screening import ScreeningEngine
from ashare_lab.warehouse import MarketRepository


def test_implemented_duplicate_and_conflicting_dividends() -> None:
    event = {
        "ann_date": "2024-01-01",
        "imp_ann_date": "2024-01-02",
        "ex_date": "2024-01-03",
        "pay_date": "2024-01-05",
        "end_date": "2023-12-31",
        "cash_div_tax": 0.5,
        "stk_div": 0,
        "div_proc": "实施",
    }
    assert FactorEngine.dividend_yield(FIXTURE_DATE, {"close": 10}, [event, event], True)[0] == 0.05
    conflict = {**event, "cash_div_tax": 0.9}
    assert (
        FactorEngine.dividend_yield(FIXTURE_DATE, {"close": 10}, [event, conflict], True)[0] is None
    )


def test_later_delisted_stock_is_in_legacy_historical_universe(
    repository: MarketRepository,
) -> None:
    data = repository.read("stock_basic", repository.db.snapshot()).with_columns(
        pl.when(pl.col("stock_code") == "000001.SZ")
        .then(pl.lit("2025-01-01"))
        .otherwise(pl.col("delist_date"))
        .alias("delist_date"),
        pl.lit("D").alias("list_status"),
    )
    repository.publish("stock_basic", data, "delisted", "fixture", "test")
    codes = [
        r["stock_code"]
        for r in UniverseManager(repository).resolve(
            FIXTURE_DATE, Universe(), repository.db.snapshot(), exclude_delisted=False
        )
    ]
    assert "000001.SZ" in codes
    future = [
        r["stock_code"]
        for r in UniverseManager(repository).resolve(
            "2025-01-01", Universe(), repository.db.snapshot()
        )
    ]
    assert "000001.SZ" not in future


def test_adjustment_at_asof_and_actual_read_bounds(repository: MarketRepository) -> None:
    snapshot = repository.db.snapshot()
    adj = repository.read("adj_factor", snapshot)
    adj = adj.with_columns(
        pl.when((pl.col("stock_code") == "000004.SZ") & (pl.col("trade_date") >= "2024-06-20"))
        .then(2.0)
        .otherwise(pl.col("adj_factor"))
        .alias("adj_factor")
    )
    repository.publish("adj_factor", adj, "split", "fixture", "test")
    engine = ScreeningEngine(repository)
    with patch.object(repository, "read", wraps=repository.read) as read:
        output = engine.run(
            ScreeningRequest(as_of_date="2024-06-19", rule_id="D"), repository.db.snapshot()
        )
        for call in read.call_args_list:
            if call.args[0] in {"daily", "adj_factor"}:
                assert call.args[4] == "2024-06-19"
    value = next(r["factor_values"] for r in output if r["stock_code"] == "000004.SZ")
    assert value["signal_close"] == value["close"]


def test_non_finite_provider_numbers_are_rejected() -> None:
    with pytest.raises(ProviderError):
        normalize(
            "adj_factor",
            pl.DataFrame(
                {"ts_code": ["000001.SZ"], "trade_date": ["20240628"], "adj_factor": [float("nan")]}
            ),
            "fixture",
            "batch",
        )


def test_missing_volume_is_unknown_not_worker_failure(repository: MarketRepository) -> None:
    data = repository.read("daily", repository.db.snapshot()).with_columns(
        pl.when((pl.col("stock_code") == "000004.SZ") & (pl.col("trade_date") == FIXTURE_DATE))
        .then(None)
        .otherwise(pl.col("volume"))
        .alias("volume")
    )
    repository.publish("daily", data, "missing-volume", "fixture", "test")
    output = ScreeningEngine(repository).run(
        ScreeningRequest(as_of_date=FIXTURE_DATE, rule_id="D"), repository.db.snapshot()
    )
    result = next(r for r in output if r["stock_code"] == "000004.SZ")
    assert result["factor_values"]["volume_ratio_5d"] is None
    assert result["match_status"] == "insufficient_data"
