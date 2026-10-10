from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl
from ashare_lab.config import Settings
from ashare_lab.factors import UniverseManager
from ashare_lab.scheduler import MonthlyNameScheduler, monthly_due
from ashare_lab.schemas import SyncRequest, Universe
from ashare_lab.services import ApplicationService
from ashare_lab.warehouse import MarketRepository
from ashare_lab.worker import Worker
from test_sync_recovery import MiniProvider


def test_first_saturday_and_catchup() -> None:
    tz = ZoneInfo("Asia/Shanghai")
    assert (
        monthly_due(datetime(2026, 10, 3, 1, 59, tzinfo=tz)).isoformat()
        == "2026-09-05T05:00:00+08:00"
    )
    assert (
        monthly_due(datetime(2026, 10, 2, 21, tzinfo=ZoneInfo("UTC"))).isoformat()
        == "2026-10-03T05:00:00+08:00"
    )
    assert monthly_due(datetime(2027, 1, 1, tzinfo=tz)).isoformat() == "2026-12-05T05:00:00+08:00"


def test_schedule_durable_once_and_disabled_in_fixture(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None, data_dir=tmp_path, database_path=tmp_path / "db", tushare_token="test-only"
    )
    service = ApplicationService(settings)
    instant = datetime(2026, 10, 9, tzinfo=ZoneInfo("Asia/Shanghai"))
    scheduler = MonthlyNameScheduler(settings, service.db, service.repository)
    scheduler.tick(instant)
    MonthlyNameScheduler(settings, service.db, service.repository).tick(instant)
    tasks = service.task_list("sync")
    assert len(tasks) == 1
    assert tasks[0]["payload"]["mode"] == "namechange"
    service.db.update(tasks[0]["id"], status="failed")
    scheduler.tick(instant)
    assert len(service.task_list("sync")) == 1
    service.repository.publish("daily", pl.DataFrame(), "empty", "fixture", "test")
    scheduler.tick(datetime(2026, 11, 8, tzinfo=instant.tzinfo))
    assert len(service.task_list("sync")) == 1
    settings.tushare_token = Settings(_env_file=None).tushare_token
    scheduler.tick(datetime(2026, 12, 8, tzinfo=instant.tzinfo))
    assert len(service.task_list("sync")) == 1


def test_daily_sync_skips_names_and_dedicated_job_only_fetches_names(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path, database_path=tmp_path / "db")
    service = ApplicationService(settings)
    provider = MiniProvider()
    worker = Worker(settings, provider)
    job = service.submit_sync(
        SyncRequest(mode="initialize", start_date="2024-06-27", end_date="2024-06-28")
    )
    worker.run_once()
    assert service.job(job["id"])["status"] == "succeeded"
    assert not any(d == "namechange" for d, _ in provider.calls)
    provider.calls.clear()
    job = service.submit_sync(SyncRequest(mode="namechange", end_date="2024-06-28"))
    worker.run_once()
    assert service.job(job["id"])["status"] == "succeeded"
    assert {d for d, _ in provider.calls} == {"stock_basic", "namechange"}
    assert all(p.get("ts_code") for d, p in provider.calls if d == "namechange")


def test_retired_excluded_but_legacy_history_available(repository: MarketRepository) -> None:
    old = repository.db.snapshot()
    basics = repository.read("stock_basic", old)
    repository.publish(
        "stock_basic",
        basics.with_columns(
            pl.when(pl.col("stock_code") == "000001.SZ")
            .then(pl.lit("D"))
            .otherwise(pl.col("list_status"))
            .alias("list_status"),
            pl.when(pl.col("stock_code") == "000001.SZ")
            .then(pl.lit("2025-01-01"))
            .otherwise(pl.col("delist_date"))
            .alias("delist_date"),
        ),
        "changed",
        "fixture",
        "test",
    )
    manager = UniverseManager(repository)
    latest = repository.db.snapshot()
    assert "000001.SZ" not in {
        r["stock_code"] for r in manager.resolve("2024-06-28", Universe(), latest)
    }
    assert "000001.SZ" in {
        r["stock_code"]
        for r in manager.resolve("2024-06-28", Universe(), latest, exclude_delisted=False)
    }
    assert "000001.SZ" in {r["stock_code"] for r in manager.resolve("2024-06-28", Universe(), old)}


def test_schedule_claim_rolls_back_with_enqueue(tmp_path: Path) -> None:
    from unittest.mock import patch

    import pytest

    settings = Settings(
        _env_file=None, data_dir=tmp_path, database_path=tmp_path / "db", tushare_token="test-only"
    )
    service = ApplicationService(settings)
    scheduler = MonthlyNameScheduler(settings, service.db, service.repository)
    instant = datetime(2026, 10, 9, tzinfo=ZoneInfo("Asia/Shanghai"))
    original = service.db.snapshot

    def failed_snapshot(connection: object = None) -> dict[str, str]:
        if connection is not None:
            raise RuntimeError("interruption")
        return original()

    with (
        patch.object(service.db, "snapshot", side_effect=failed_snapshot),
        pytest.raises(RuntimeError),
    ):
        scheduler.tick(instant)
    assert service.task_list("sync") == []
    scheduler.tick(instant)
    assert len(service.task_list("sync")) == 1


def test_name_targets_exceed_5000_without_global_query(tmp_path: Path) -> None:
    from unittest.mock import patch

    from ashare_lab.db import Database
    from ashare_lab.sync import SyncService

    class LargeProvider(MiniProvider):
        def fetch(self, dataset: str, params: dict[str, str]) -> pl.DataFrame:
            if dataset == "namechange":
                self.calls.append((dataset, params))
                return pl.DataFrame()
            return super().fetch(dataset, params)

    db = Database(tmp_path / "db")
    db.initialize()
    repository = MarketRepository(db, tmp_path)
    codes = [f"{i:06d}.SZ" for i in range(1, 5002)]
    basics = pl.DataFrame(
        {
            "stock_code": codes,
            "exchange": ["SZSE"] * len(codes),
            "list_status": ["L"] * len(codes),
            "list_date": ["2000-01-01"] * len(codes),
        }
    )
    job = db.enqueue(
        "sync",
        {"mode": "namechange", "end_date": "2024-06-28"},
        {"_sync_policy": "monthly_names_v1"},
    )
    provider = LargeProvider()
    with (
        patch.object(repository, "read", return_value=basics),
        patch.object(repository, "publish", return_value="test"),
    ):
        SyncService(db, repository, provider, concurrency=4).run(job, lambda: None)
    calls = [p for d, p in provider.calls if d == "namechange"]
    assert len(calls) == 5001
    assert {p["ts_code"] for p in calls} == set(codes)


def test_new_screen_policy_and_name_status_are_public(
    repository: MarketRepository, tmp_path: Path
) -> None:
    from ashare_lab.fixtures import FIXTURE_DATE
    from ashare_lab.schemas import ScreeningRequest

    settings = Settings(
        _env_file=None, data_dir=repository.root, database_path=tmp_path / "business.db"
    )
    service = ApplicationService(settings)
    job = service.submit_screen(ScreeningRequest(as_of_date=FIXTURE_DATE, rule_id="A"))
    assert job["snapshot"]["_universe_policy"] == "survivors_v1"
    status = service.market_status()
    assert status["namechange_schedule"]["enabled"] is False
    assert status["namechange_schedule"]["timezone"] == "Asia/Shanghai"
    Worker(settings).run_once()
    assert "universe_policy" in service.export(job["id"]).splitlines()[0]
    assert "排除数据版本中已退市证券" in service.export(job["id"])


def test_sync_dates_use_shanghai_at_early_morning() -> None:
    from unittest.mock import patch

    import pytest
    from pydantic import ValidationError

    instant = datetime(2026, 10, 3, 2, tzinfo=ZoneInfo("Asia/Shanghai"))
    with patch("ashare_lab.schemas.datetime") as clock:
        clock.now.return_value = instant
        assert SyncRequest(mode="namechange", end_date="2026-10-03").end_date == instant.date()
        with pytest.raises(ValidationError):
            SyncRequest(mode="namechange", end_date="2026-10-04")
