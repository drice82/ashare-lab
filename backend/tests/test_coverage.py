import polars as pl
from ashare_lab.db import coverage_entries, versions
from ashare_lab.fixtures import FIXTURE_DATE
from ashare_lab.schemas import ScreeningRequest
from ashare_lab.screening import ScreeningEngine
from ashare_lab.warehouse import MarketRepository
from sqlalchemy import func, select


def test_coverage_watermark_is_immutable_and_storage_linear(repository: MarketRepository) -> None:
    first = repository.publish(
        "test_empty",
        pl.DataFrame(),
        "day:0",
        "fixture",
        "batch",
        coverage_info={"trade_date": "2024-01-01"},
    )
    for i in range(1, 101):
        repository.publish("test_empty", pl.DataFrame(), f"day:{i}", "fixture", "batch")
    last = repository.db.snapshot()["test_empty"]
    assert len(repository.version(first)["coverage"]) == 1
    assert len(repository.version(last)["coverage"]) == 101
    assert list(repository.version(last, resolve_coverage=False)["coverage"]) == ["watermark"]
    with repository.db.engine.connect() as conn:
        assert (
            conn.execute(
                select(func.count())
                .select_from(coverage_entries)
                .where(coverage_entries.c.dataset == "test_empty")
            ).scalar_one()
            == 101
        )


def test_legacy_coverage_is_preserved_when_new_version_is_published(
    repository: MarketRepository,
) -> None:
    first = repository.publish("legacy", pl.DataFrame(), "old", "fixture", "batch")
    with repository.db.engine.begin() as conn:
        conn.execute(
            versions.update()
            .where(versions.c.id == first)
            .values(coverage={"old": {"complete": True}})
        )
    latest = repository.publish("legacy", pl.DataFrame(), "new", "fixture", "batch")
    assert set(repository.version(first)["coverage"]) == {"old"}
    assert set(repository.version(latest)["coverage"]) == {"old", "new"}


def test_stale_dividend_coverage_does_not_pass_historical_yield(
    repository: MarketRepository,
) -> None:
    repository.publish(
        "dividend",
        pl.DataFrame(),
        "stock:000001.SZ",
        "fixture",
        "stale",
        coverage_info={"as_of_date": "2024-06-27"},
    )
    output = ScreeningEngine(repository).run(
        ScreeningRequest(as_of_date=FIXTURE_DATE, rule_id="A"), repository.db.snapshot()
    )
    result = next(row for row in output if row["stock_code"] == "000001.SZ")
    assert result["factor_values"]["dividend_yield_ttm"] is None
    assert result["match_status"] == "insufficient_data"
