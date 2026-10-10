from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import polars as pl
import pytest
from ashare_lab.config import Settings
from ashare_lab.scheduler import DailyDividendScheduler, daily_due, monthly_due
from ashare_lab.schemas import SyncRequest
from ashare_lab.services import ApplicationService
from ashare_lab.worker import Worker
from test_sync_recovery import MiniProvider

TZ = ZoneInfo("Asia/Shanghai")


def test_schedule_boundaries() -> None:
    assert (
        daily_due(datetime(2026, 1, 1, 0, 59, tzinfo=TZ)).isoformat() == "2025-12-31T01:00:00+08:00"
    )
    assert daily_due(datetime(2026, 1, 1, 1, tzinfo=TZ)).isoformat() == "2026-01-01T01:00:00+08:00"
    assert (
        daily_due(datetime(2025, 12, 31, 17, tzinfo=ZoneInfo("UTC"))).isoformat()
        == "2026-01-01T01:00:00+08:00"
    )
    assert (
        monthly_due(datetime(2026, 10, 3, 4, 59, tzinfo=TZ)).isoformat()
        == "2026-09-05T05:00:00+08:00"
    )
    assert (
        monthly_due(datetime(2026, 10, 2, 21, tzinfo=ZoneInfo("UTC"))).isoformat()
        == "2026-10-03T05:00:00+08:00"
    )


def test_daily_durable_catchup_and_rollback(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None, data_dir=tmp_path, database_path=tmp_path / "db", tushare_token="test-only"
    )
    service = ApplicationService(settings)
    scheduler = DailyDividendScheduler(settings, service.db, service.repository)
    instant = datetime(2026, 10, 9, 0, 59, tzinfo=TZ)
    original = service.db.snapshot

    def fail(connection: object = None) -> dict[str, str]:
        if connection is not None:
            raise RuntimeError("interruption")
        return original()

    with patch.object(service.db, "snapshot", side_effect=fail), pytest.raises(RuntimeError):
        scheduler.tick(instant)
    assert service.task_list("sync") == []
    scheduler.tick(instant)
    DailyDividendScheduler(settings, service.db, service.repository).tick(instant)
    job = service.task_list("sync")[0]
    assert job["payload"] == {
        "mode": "dividend",
        "end_date": "2026-10-07",
        "history_refresh": "incremental",
    }
    service.db.update(job["id"], status="partial")
    scheduler.tick(instant)
    status = scheduler.status(instant)
    assert status["last_completed"] is None
    assert status["latest_job"]["status"] == "partial"
    assert status["next_run"] == "2026-10-09T01:00:00+08:00"
    scheduler.tick(datetime(2026, 10, 12, 1, tzinfo=TZ))
    assert len(service.task_list("sync")) == 2  # Only the latest missed date.
    service.db.update(job["id"], status="succeeded")
    assert scheduler.status(instant)["last_completed"] is not None
    settings.tushare_token = type(settings.tushare_token)("")
    scheduler.tick(datetime(2026, 10, 13, 1, tzinfo=TZ))
    assert len(service.task_list("sync")) == 2
    settings.tushare_token = type(settings.tushare_token)("test-only")
    service.repository.publish("daily", pl.DataFrame(), "empty", "fixture", "test")
    scheduler.tick(datetime(2026, 10, 14, 1, tzinfo=TZ))
    assert not scheduler.status()["enabled"]
    assert len(service.task_list("sync")) == 2


def test_new_incremental_skips_dividend_but_old_policy_retains_it(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path, database_path=tmp_path / "db")
    service = ApplicationService(settings)
    provider = MiniProvider()
    worker = Worker(settings, provider)
    job = service.submit_sync(
        SyncRequest(mode="initialize", start_date="2024-06-27", end_date="2024-06-28")
    )
    worker.run_once()
    assert service.job(job["id"])["status"] == "succeeded"
    assert any(d == "dividend" for d, _ in provider.calls)
    provider.calls.clear()
    job = service.submit_sync(SyncRequest(mode="incremental", end_date="2024-06-28"))
    worker.run_once()
    assert service.job(job["id"])["status"] == "succeeded"
    assert not any(d in {"dividend", "namechange"} for d, _ in provider.calls)
    provider.calls.clear()
    job = service.db.enqueue(
        "sync",
        {"mode": "incremental", "end_date": "2024-06-28"},
        {"_sync_policy": "monthly_names_v1"},
    )
    worker.run_once()
    assert service.job(job["id"])["status"] == "succeeded"
    assert any(d == "dividend" for d, _ in provider.calls)
    assert not any(d == "namechange" for d, _ in provider.calls)


@pytest.mark.parametrize("interruption", ["cancel", "restart"])
def test_dividend_only_cancel_resume_and_old_versions(tmp_path: Path, interruption: str) -> None:
    class HistoryProvider(MiniProvider):
        def fetch(self, dataset: str, params: dict[str, str]) -> pl.DataFrame:
            frame = super().fetch(dataset, params)
            if dataset == "stock_basic" and frame.height:
                return pl.concat([frame, frame.with_columns(pl.lit("000002.SZ").alias("ts_code"))])
            if dataset == "dividend" and frame.height:
                return frame.with_columns(pl.lit(params["ts_code"]).alias("ts_code"))
            return frame

    settings = Settings(
        _env_file=None, data_dir=tmp_path, database_path=tmp_path / "db", sync_concurrency=1
    )
    service = ApplicationService(settings)
    provider = HistoryProvider()
    worker = Worker(settings, provider)
    job = service.submit_sync(SyncRequest(mode="dividend", end_date="2024-06-28"))
    publish = worker.repository.publish

    def interrupt(*args: object, **kwargs: object) -> str:
        version = publish(*args, **kwargs)
        if args[0] == "dividend":
            if interruption == "restart":
                raise SystemExit("simulated process interruption")
            service.cancel(job["id"], "sync")
        return version

    with patch.object(worker.repository, "publish", side_effect=interrupt):
        if interruption == "restart":
            with pytest.raises(SystemExit):
                worker.run_once()
        else:
            worker.run_once()
    assert service.job(job["id"])["status"] == (
        "running" if interruption == "restart" else "cancelled"
    )
    old = service.db.snapshot()
    old_rows = service.repository.read("dividend", old).to_dicts()
    pinned = service.job(job["id"])["snapshot"]["_dividend_codes"]
    basics = service.repository.read("stock_basic", old)
    service.repository.publish(
        "stock_basic",
        basics.head(1).with_columns(pl.lit("000003.SZ").alias("stock_code")),
        "new",
        "fixture",
        "external",
    )
    provider.calls.clear()
    if interruption == "restart":
        worker.recover()
    else:
        service.resume(job["id"])
    worker.run_once()
    outcome = service.job(job["id"])
    assert outcome["status"] == "succeeded"
    assert outcome["snapshot"]["_dividend_codes"] == pinned
    assert outcome["result"]["end_date"] == "2024-06-28"
    assert {d for d, _ in provider.calls} <= {"stock_basic", "dividend"}
    assert [p["ts_code"] for d, p in provider.calls if d == "dividend"] == ["000002.SZ"]
    assert service.repository.read("dividend", old).to_dicts() == old_rows
    count = (
        service.repository.read("dividend", service.db.snapshot())
        .filter(pl.col("stock_code").is_in(["000001.SZ", "000002.SZ"]))
        .height
    )
    service.submit_sync(SyncRequest(mode="dividend", end_date="2024-06-28"))
    worker.run_once()
    assert (
        service.repository.read("dividend", service.db.snapshot())
        .filter(pl.col("stock_code").is_in(["000001.SZ", "000002.SZ"]))
        .height
        == count
    )
    assert service.market_status()["dividend_schedule"]["schedule"] == "每天 01:00"


def test_dividend_permission_denial_is_partial(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path, database_path=tmp_path / "db")
    service = ApplicationService(settings)
    job = service.submit_sync(SyncRequest(mode="dividend"))
    Worker(settings, MiniProvider(deny_optional=True)).run_once()
    assert service.job(job["id"])["status"] == "partial"
    assert "dividend" not in service.db.snapshot()


@pytest.mark.parametrize(
    "policy,expected", [("daily_dividends_v2", False), ("monthly_names_v1", False), (None, True)]
)
def test_sync_policy_stock_pool_compatibility(
    tmp_path: Path, policy: str | None, expected: bool
) -> None:
    class RetiredProvider(MiniProvider):
        def fetch(self, dataset: str, params: dict[str, str]) -> pl.DataFrame:
            frame = super().fetch(dataset, params)
            if dataset == "stock_basic" and params.get("list_status") == "D":
                listed = super().fetch(dataset, {"list_status": "L"})
                return listed.with_columns(
                    pl.lit("000002.SZ").alias("ts_code"),
                    pl.lit("D").alias("list_status"),
                    pl.lit("20250101").alias("delist_date"),
                )
            if dataset == "dividend" and frame.height:
                return frame.with_columns(pl.lit(params["ts_code"]).alias("ts_code"))
            return frame

    settings = Settings(_env_file=None, data_dir=tmp_path, database_path=tmp_path / "db")
    service = ApplicationService(settings)
    provider = RetiredProvider()
    worker = Worker(settings, provider)
    job = service.db.enqueue(
        "sync",
        {"mode": "dividend", "end_date": "2024-06-28"},
        {"_sync_policy": policy} if policy else {},
    )
    worker.run_once()
    assert service.job(job["id"])["status"] == "succeeded"
    assert (
        any(d == "dividend" and p["ts_code"] == "000002.SZ" for d, p in provider.calls) == expected
    )


def test_monthly_claim_retained_and_background_jobs_registered(tmp_path: Path) -> None:
    from ashare_lab.db import settings_table
    from ashare_lab.scheduler import MonthlyNameScheduler, create_scheduler

    settings = Settings(
        _env_file=None, data_dir=tmp_path, database_path=tmp_path / "db", tushare_token="test-only"
    )
    service = ApplicationService(settings)
    with service.db.engine.begin() as conn:
        conn.execute(
            settings_table.insert().values(key="namechange:2026-10", value={"job_id": "old"})
        )
    MonthlyNameScheduler(settings, service.db, service.repository).tick(
        datetime(2026, 10, 3, 5, tzinfo=TZ)
    )
    assert service.task_list("sync") == []
    scheduler = create_scheduler(settings, service.db, service.repository)
    assert {job.id for job in scheduler.get_jobs()} == {
        "monthly_names",
        "daily_dividends",
        "daily_incremental",
        "monthly_dividends",
        "daily_names",
    }


def test_dividend_api_and_future_date_validation(tmp_path: Path) -> None:
    from ashare_lab.api import create_app
    from fastapi.testclient import TestClient

    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        database_path=tmp_path / "db",
        data_management_password="test-management",
    )
    app = create_app(settings)
    client = TestClient(app, headers={"X-Data-Management-Password": "test-management"})
    response = client.post("/api/v1/data/sync", json={"mode": "dividend", "end_date": "2024-06-28"})
    assert response.status_code == 202
    worker = Worker(settings, MiniProvider())
    worker.run_once()
    assert client.get(f"/api/v1/data/sync/{response.json()['id']}").json()["status"] == "succeeded"
    status = client.get("/api/v1/market/status").json()
    assert status["dividend_schedule"]["schedule"] == "每天 01:00"
    assert status["namechange_schedule"]["schedule"] == "每天 02:00"
    assert (
        client.post(
            "/api/v1/data/sync", json={"mode": "dividend", "end_date": "2099-01-01"}
        ).status_code
        == 422
    )
