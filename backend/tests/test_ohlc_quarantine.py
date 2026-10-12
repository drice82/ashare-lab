import json
from pathlib import Path

import polars as pl
import pytest
from ashare_lab.config import Settings
from ashare_lab.factors import FactorEngine
from ashare_lab.providers import ProviderError, normalize
from ashare_lab.schemas import SyncRequest
from ashare_lab.services import ApplicationService
from ashare_lab.warehouse import MarketRepository
from ashare_lab.worker import Worker
from test_sync_delisted_prices import DelistedPriceProvider


def bad_quote() -> pl.DataFrame:
    return pl.DataFrame(
        [
            dict(
                ts_code="000011.SZ",
                trade_date="19920713",
                open=23.85,
                high=23.7,
                low=22.8,
                close=24.4,
                pre_close=23.85,
                vol=100,
                amount=200,
                pct_chg=2.3,
            )
        ]
    )


def test_quarantine_preserves_original_and_never_supplies_computable_prices() -> None:
    raw = bad_quote()
    with pytest.raises(ProviderError, match="000011.SZ"):
        normalize("daily", raw, "tushare", "strict")
    row = normalize("daily", raw, "tushare", "isolated", quarantine_ohlc=True).row(0, named=True)
    assert row["quality_reason"] == "ohlc_inconsistent"
    assert json.loads(row["raw_ohlc"]) == dict(
        open=23.85, high=23.7, low=22.8, close=24.4, pre_close=23.85
    )
    assert all(
        row[field] is None
        for field in (
            "open",
            "high",
            "low",
            "close",
            "pre_close",
            "volume",
            "amount",
            "daily_return",
        )
    )
    assert row["stock_code"] == "000011.SZ"
    assert row["trade_date"] == "1992-07-13"
    assert raw["close"].item() == 24.4


def test_quarantine_does_not_hide_other_schema_errors() -> None:
    raw = bad_quote().with_columns(pl.lit(None, dtype=pl.Float64).alias("pre_close"))
    with pytest.raises(ProviderError):
        normalize("daily", raw, "fixture", "bad", quarantine_ohlc=True)


def test_quarantine_resume_repeat_correction_and_old_versions(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path, database_path=tmp_path / "business.db")
    service = ApplicationService(settings)
    provider = DelistedPriceProvider(fail=True)
    provider.bad_survivor = True
    worker = Worker(settings, provider)
    request = SyncRequest(mode="initialize", start_date="2024-06-27", end_date="2024-06-28")
    job = service.submit_sync(request)
    worker.run_once()
    assert service.job(job["id"])["status"] == "failed"
    old = service.db.snapshot()
    rows = service.repository.read("daily", old).to_dicts()
    assert len(rows) == 1 and rows[0]["close"] is None
    provider.fail = False
    provider.calls.clear()
    service.resume(job["id"])
    worker.run_once()
    outcome = service.job(job["id"])
    assert outcome["status"] == "partial"
    assert any("OHLC" in warning and "2" in warning for warning in outcome["result"]["warnings"])
    assert not any(d == "daily" and p.get("trade_date") == "20240627" for d, p in provider.calls)
    coverage = service.repository.coverage("daily", service.db.snapshot())
    assert coverage["2024-06-27"]["quarantined_ohlc"][0]["stock_code"] == "000001.SZ"
    assert coverage["2024-06-27"]["quality_status"] == "degraded"
    service.submit_sync(request)
    worker.run_once()
    assert service.repository.read("daily", service.db.snapshot()).height == 2
    provider.bad_survivor = False
    corrected = service.submit_sync(request)
    worker.run_once()
    assert service.job(corrected["id"])["status"] == "succeeded"
    assert service.repository.read("daily", service.db.snapshot())["close"].null_count() == 0
    assert service.repository.read("daily", old).to_dicts() == rows


def test_quarantined_bar_propagates_unknown_to_factors(repository: MarketRepository) -> None:
    raw = bad_quote().with_columns(
        pl.lit("000004.SZ").alias("ts_code"), pl.lit("20240628").alias("trade_date")
    )
    repository.publish(
        "daily",
        normalize("daily", raw, "fixture", "bad", quarantine_ohlc=True),
        "2024-06-28",
        "fixture",
        "bad",
    )
    engine = FactorEngine(repository)
    instruments = (
        repository.read("stock_basic", repository.db.snapshot())
        .filter(pl.col("stock_code") == "000004.SZ")
        .to_dicts()
    )
    result = engine.compute("2024-06-28", instruments, repository.db.snapshot())[0]
    assert result["factor_values"]["close"] is None
    assert result["factor_values"]["ma20"] is None
    assert result["factor_values"]["is_suspended"] is None
    assert "ohlc_inconsistent" in result["data_quality_flags"]
    assert result["factor_values"]["valid_days_20d"] == 19


def test_quarantined_prices_stop_backtest_and_dca_without_returns() -> None:
    from ashare_lab.backtest import simulate as backtest
    from ashare_lab.dca import simulate as dca
    from test_backtest import market as backtest_market
    from test_backtest import request as backtest_request
    from test_dca import market as dca_market
    from test_dca import request as dca_request

    row = normalize("daily", bad_quote(), "fixture", "bad", quarantine_ohlc=True).row(0, named=True)
    market = backtest_market()
    market["bars"]["2024-01-04"] = row
    result = backtest(backtest_request(), market)
    assert result["summary"]["complete"] is False
    assert result["summary"]["total_assets"] is None
    assert not any(r["event"] == "buy" for r in result["ledger"])
    market = dca_market()
    market["bars"]["2024-01-31"] = row
    result = dca(dca_request(), market)
    assert result["summary"]["total_assets"] is None
    assert not any(r["event"] == "buy" for r in result["ledger"])


def test_quarantined_bar_is_unknown_for_every_signal_strategy(repository: MarketRepository) -> None:
    from ashare_lab.schemas import StrategyConfig
    from ashare_lab.signals import calculate_signal

    raw = bad_quote().with_columns(
        pl.lit("000004.SZ").alias("ts_code"), pl.lit("20240628").alias("trade_date")
    )
    repository.publish(
        "daily",
        normalize("daily", raw, "fixture", "bad", quarantine_ohlc=True),
        "2024-06-28",
        "fixture",
        "bad",
    )
    for strategy in (
        "ma_cross",
        "macd_cross",
        "rsi_reversal",
        "donchian_breakout",
        "bollinger_reversion",
    ):
        result = calculate_signal(
            repository,
            "000004.SZ",
            StrategyConfig(id=strategy),
            "2024-06-28",
            repository.db.snapshot(),
            "test",
        )
        assert result["status"] == "insufficient_data"
