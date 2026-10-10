from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import polars as pl
import pytest
from ashare_lab.config import Settings
from ashare_lab.scheduler import DailyIncrementalScheduler, create_scheduler, daily_due
from ashare_lab.services import ApplicationService
from ashare_lab.worker import Worker
from test_sync_recovery import MiniProvider

TZ = ZoneInfo("Asia/Shanghai")


def system(tmp_path: Path) -> tuple[ApplicationService, DailyIncrementalScheduler]:
    settings = Settings(
        _env_file=None, data_dir=tmp_path, database_path=tmp_path / "db", tushare_token="test-only"
    )
    service = ApplicationService(settings)
    service.repository.publish(
        "daily",
        pl.DataFrame({"stock_code": ["000001.SZ"], "trade_date": ["2024-06-27"], "close": [10.0]}),
        "2024-06-27",
        "tushare",
        "seed",
    )
    return service, DailyIncrementalScheduler(settings, service.db, service.repository)


def test_close_schedule_shanghai_boundaries() -> None:
    assert (
        daily_due(datetime(2026, 1, 1, 15, 59, tzinfo=TZ), hour=16).isoformat()
        == "2025-12-31T16:00:00+08:00"
    )
    assert (
        daily_due(datetime(2026, 1, 1, 16, tzinfo=TZ), hour=16).isoformat()
        == "2026-01-01T16:00:00+08:00"
    )
    assert (
        daily_due(datetime(2026, 1, 1, 8, tzinfo=ZoneInfo("UTC")), hour=16).isoformat()
        == "2026-01-01T16:00:00+08:00"
    )


def test_incremental_durable_date_catchup_and_rollback(tmp_path: Path) -> None:
    service, scheduler = system(tmp_path)
    instant = datetime(2024, 6, 28, 16, tzinfo=TZ)
    original = service.db.snapshot

    def fail(connection: object = None) -> dict[str, str]:
        if connection is not None:
            raise RuntimeError("interruption")
        return original()

    with patch.object(service.db, "snapshot", side_effect=fail), pytest.raises(RuntimeError):
        scheduler.tick(instant)
    assert service.task_list("sync") == []
    scheduler.tick(instant)
    DailyIncrementalScheduler(scheduler.settings, service.db, service.repository).tick(instant)
    tasks = service.task_list("sync")
    assert len(tasks) == 1
    job = tasks[0]
    assert job["payload"] == {"mode": "incremental", "end_date": "2024-06-28"}
    assert job["snapshot"]["_sync_policy"] == "history_incremental_v3"
    service.db.update(job["id"], status="failed")
    scheduler.tick(instant)
    assert len(service.task_list("sync")) == 1
    assert scheduler.status(instant)["last_completed"] is None
    assert scheduler.status(instant)["next_run"] == "2024-06-29T16:00:00+08:00"
    scheduler.tick(datetime(2024, 7, 1, 15, 59, tzinfo=TZ))
    tasks = service.task_list("sync")
    assert len(tasks) == 2
    assert {t["payload"]["end_date"] for t in tasks} == {"2024-06-28", "2024-06-30"}


def test_incremental_enabled_requires_real_daily_and_token(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None, data_dir=tmp_path, database_path=tmp_path / "db", tushare_token="test-only"
    )
    service = ApplicationService(settings)
    scheduler = DailyIncrementalScheduler(settings, service.db, service.repository)
    assert not scheduler.enabled()
    scheduler.tick()
    assert not service.task_list("sync")
    service.repository.publish(
        "daily",
        pl.DataFrame({"stock_code": ["000001.SZ"], "trade_date": ["2024-06-28"]}),
        "day",
        "tushare",
        "seed",
    )
    assert scheduler.enabled()
    settings.tushare_token = type(settings.tushare_token)("")
    assert not scheduler.enabled()
    settings.tushare_token = type(settings.tushare_token)("test-only")
    service.repository.publish("dividend", pl.DataFrame(), "empty", "fixture", "test")
    assert not scheduler.enabled()


def test_scheduled_incremental_syncs_same_day_before_22_and_status(tmp_path: Path) -> None:
    service, scheduler = system(tmp_path)
    scheduler.tick(datetime(2024, 6, 28, 16, tzinfo=TZ))
    provider = MiniProvider()
    # Real-source metadata isolation is checked separately; fixture calls here are explicit test data.
    worker = Worker(scheduler.settings, provider)
    with patch("ashare_lab.sync.datetime") as clock:
        clock.now.return_value = datetime(2024, 6, 28, 16, tzinfo=TZ)
        clock.strptime.side_effect = datetime.strptime
        worker.run_once()
    job = service.task_list("sync")[0]
    assert job["status"] == "succeeded"
    assert any(d == "daily" and p.get("trade_date") == "20240628" for d, p in provider.calls)
    assert not any(d in {"dividend", "namechange"} for d, _ in provider.calls)
    status = service.market_status()["incremental_schedule"]
    assert status["schedule"] == "每天 16:00"
    assert status["last_completed"] is not None
    assert status["latest_job"]["status"] == "succeeded"
    assert {
        job.id
        for job in create_scheduler(scheduler.settings, service.db, service.repository).get_jobs()
    } == {
        "monthly_names",
        "daily_dividends",
        "daily_incremental",
        "monthly_dividends",
        "daily_names",
    }
