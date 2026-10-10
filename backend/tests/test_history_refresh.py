from pathlib import Path
from unittest.mock import patch

import polars as pl
import pytest
from ashare_lab.api import create_app
from ashare_lab.config import Settings
from ashare_lab.providers import normalize
from ashare_lab.schemas import SyncRequest
from ashare_lab.services import ApplicationService
from ashare_lab.worker import Worker
from fastapi.testclient import TestClient
from test_sync_recovery import MiniProvider


def test_management_password_and_independent_research(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        database_path=tmp_path / "db",
        data_management_password="test-management",
    )
    client = TestClient(create_app(settings))
    for path in [
        "/data/status",
        "/data/datasets",
        "/data/sync/missing",
        "/jobs",
        "/jobs?kind=sync",
    ]:
        assert client.get("/api/v1" + path).status_code == 403
        assert (
            client.get(
                "/api/v1" + path, headers={"X-Data-Management-Password": "wrong"}
            ).status_code
            == 403
        )
    for path in ["/data/sync", "/data/sync/missing/cancel", "/data/sync/missing/resume"]:
        assert client.post("/api/v1" + path, json={}).status_code == 403
    assert (
        client.get(
            "/api/v1/data/status", headers={"X-Data-Management-Password": "test-management"}
        ).status_code
        == 200
    )
    assert client.get("/api/v1/market/status").status_code == 200
    assert client.get("/api/v1/jobs?kind=screening").status_code == 200
    assert "test-management" not in repr(settings)
    settings.data_management_password = type(settings.data_management_password)("")
    assert client.get("/api/v1/data/status").status_code == 403


class ChangedProvider(MiniProvider):
    empty = False
    changed = False

    def fetch(self, dataset: str, params: dict[str, str]) -> pl.DataFrame:
        if dataset in {"dividend", "namechange"} and not params.get("ts_code"):
            self.calls.append((dataset, params))
            return pl.DataFrame({"ts_code": ["000001.SZ"]})
        frame = super().fetch(dataset, params)
        if dataset == "dividend" and self.empty:
            return pl.DataFrame()
        if dataset == "dividend" and self.changed and frame.height:
            return frame.with_columns(
                pl.lit("20240627").alias("ex_date"), pl.lit(0.7).alias("cash_div_tax")
            )
        return frame


def test_incremental_replaces_revision_keeps_old_version_and_scan_watermark(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path, database_path=tmp_path / "db")
    service = ApplicationService(settings)
    provider = ChangedProvider()
    worker = Worker(settings, provider)
    service.submit_sync(SyncRequest(mode="dividend", history_refresh="full", end_date="2024-06-28"))
    worker.run_once()
    old = service.db.snapshot()
    old_rows = service.repository.read("dividend", old).to_dicts()
    provider.changed = True
    provider.calls.clear()
    job = service.submit_sync(SyncRequest(mode="dividend", end_date="2024-07-01"))
    worker.run_once()
    assert service.job(job["id"])["status"] == "succeeded"
    rows = service.repository.read("dividend", service.db.snapshot()).to_dicts()
    assert len(rows) == len(old_rows)
    assert rows[0]["ex_date"] == "2024-06-27"
    assert rows[0]["cash_div_tax"] == 0.7
    assert service.repository.read("dividend", old).to_dicts() == old_rows
    queries = [p for d, p in provider.calls if d == "dividend" and not p.get("ts_code")]
    assert {next(iter(p)) for p in queries} == {
        "ann_date",
        "imp_ann_date",
        "record_date",
        "ex_date",
    }
    assert min(next(iter(p.values())) for p in queries) == "20240529"
    assert "000001.SZ" in service.repository.history_complete_codes(
        "dividend", "2024-07-01", service.db.snapshot()
    )
    provider.empty = True
    failed = service.submit_sync(SyncRequest(mode="dividend", end_date="2024-07-03"))
    worker.run_once()
    assert service.job(failed["id"])["status"] == "failed"
    assert (
        service.repository.coverage("dividend", service.db.snapshot())["history:observation"][
            "as_of_date"
        ]
        == "2024-07-01"
    )
    assert service.repository.read("dividend", service.db.snapshot()).to_dicts() == rows


def test_replacement_interruption_before_commit_and_repeat(tmp_path: Path) -> None:
    service = ApplicationService(
        Settings(_env_file=None, data_dir=tmp_path, database_path=tmp_path / "db")
    )
    frame = normalize(
        "dividend", MiniProvider().fetch("dividend", {"ts_code": "000001.SZ"}), "fixture", "test"
    )
    service.repository.publish("dividend", frame, "stock:000001.SZ", "fixture", "test")
    old = service.db.snapshot()
    revised = frame.with_columns(pl.lit("2024-06-27").alias("ex_date"))
    with (
        patch("ashare_lab.warehouse.os.replace", side_effect=OSError("interrupted")),
        pytest.raises(OSError),
    ):
        service.repository.publish(
            "dividend", revised, "stock:000001.SZ", "fixture", "test", replace_codes=["000001.SZ"]
        )
    assert service.db.snapshot() == old
    for _ in range(2):
        service.repository.publish(
            "dividend", revised, "stock:000001.SZ", "fixture", "test", replace_codes=["000001.SZ"]
        )
    assert service.repository.read("dividend", service.db.snapshot()).height == frame.height
    assert service.repository.read("dividend", old).to_dicts() == frame.to_dicts()


def test_history_refresh_only_for_history() -> None:
    with pytest.raises(ValueError):
        SyncRequest(mode="incremental", history_refresh="full")


@pytest.mark.parametrize("interruption", ["scan", "stock", "commit"])
def test_pinned_recovery_and_atomic_observation(tmp_path: Path, interruption: str) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path, database_path=tmp_path / "db")
    service = ApplicationService(settings)
    provider = ChangedProvider()
    worker = Worker(settings, provider)
    service.submit_sync(SyncRequest(mode="dividend", history_refresh="full", end_date="2024-06-28"))
    worker.run_once()
    job = service.submit_sync(SyncRequest(mode="dividend", end_date="2024-08-02"))
    publish, update = worker.repository.publish, worker.db.update
    stopped = False

    def stop_publish(*args: object, **kwargs: object) -> str:
        version = publish(*args, **kwargs)
        if args[0] == "dividend" and (
            (interruption == "stock" and str(args[2]).startswith("stock:"))
            or (interruption == "commit" and args[2] == "history:observation")
        ):
            raise SystemExit("process interrupted after commit")
        return version

    def stop_scan(*args: object, **kwargs: object) -> None:
        nonlocal stopped
        update(*args, **kwargs)
        snapshot = kwargs.get("snapshot", {})
        if (
            interruption == "scan"
            and not stopped
            and snapshot.get("_dividend_refresh", {}).get("queries")
        ):
            stopped = True
            raise SystemExit("process interrupted after scan checkpoint")

    with (
        patch.object(worker.repository, "publish", side_effect=stop_publish),
        patch.object(worker.db, "update", side_effect=stop_scan),
        pytest.raises(SystemExit),
    ):
        worker.run_once()
    pinned = service.job(job["id"])["snapshot"]["_dividend_refresh"]
    assert pinned["start"] == "2024-05-29"  # across-month outage is not shortened to 30 days
    if interruption == "commit":
        assert service.job(job["id"])["status"] == "succeeded"
    else:
        assert (
            service.repository.coverage("dividend", service.db.snapshot())["history:observation"][
                "as_of_date"
            ]
            == "2024-06-28"
        )
        provider.calls.clear()
        worker.recover()
        worker.run_once()
        assert service.job(job["id"])["status"] == "succeeded"
        if interruption == "scan":
            assert not any(
                d == "dividend" and p == {"ann_date": "20240529"} for d, p in provider.calls
            )
        if interruption == "stock":
            assert not any(d == "dividend" for d, _ in provider.calls)
    assert (
        service.repository.coverage("dividend", service.db.snapshot())["history:observation"][
            "as_of_date"
        ]
        == "2024-08-02"
    )


def test_name_changes_replace_prior_end_and_keep_unknown_end(tmp_path: Path) -> None:
    class Names(MiniProvider):
        revised = False

        def fetch(self, dataset: str, params: dict[str, str]) -> pl.DataFrame:
            if dataset == "namechange":
                self.calls.append((dataset, params))
                if not params.get("ts_code"):
                    return pl.DataFrame({"ts_code": ["000001.SZ"]})
                return pl.DataFrame(
                    [
                        {
                            "ts_code": "000001.SZ",
                            "name": "ST测试",
                            "start_date": "20200101",
                            "end_date": "20240628" if self.revised else None,
                            "ann_date": "20200101",
                        },
                        *(
                            [
                                {
                                    "ts_code": "000001.SZ",
                                    "name": "测试",
                                    "start_date": "20240629",
                                    "end_date": None,
                                    "ann_date": "20240629",
                                }
                            ]
                            if self.revised
                            else []
                        ),
                    ]
                )
            return super().fetch(dataset, params)

    settings = Settings(_env_file=None, data_dir=tmp_path, database_path=tmp_path / "db")
    service, provider = ApplicationService(settings), Names()
    worker = Worker(settings, provider)
    service.submit_sync(
        SyncRequest(mode="namechange", history_refresh="full", end_date="2024-06-28")
    )
    worker.run_once()
    old = service.db.snapshot()
    provider.revised = True
    service.submit_sync(SyncRequest(mode="namechange", end_date="2024-07-01"))
    worker.run_once()
    rows = (
        service.repository.read("namechange", service.db.snapshot()).sort("start_date").to_dicts()
    )
    assert len(rows) == 2 and rows[0]["end_date"] == "2024-06-28" and rows[1]["end_date"] is None
    assert service.repository.read("namechange", old)["end_date"].to_list() == [None]


def test_truncated_discovery_falls_back_to_full_stocks(tmp_path: Path) -> None:
    from ashare_lab.providers import LIMITS

    class Overflow(ChangedProvider):
        def fetch(self, dataset: str, params: dict[str, str]) -> pl.DataFrame:
            if dataset == "namechange" and not params.get("ts_code"):
                self.calls.append((dataset, params))
                return pl.DataFrame({"ts_code": ["000001.SZ"] * LIMITS[dataset]})
            return super().fetch(dataset, params)

    settings = Settings(_env_file=None, data_dir=tmp_path, database_path=tmp_path / "db")
    service, provider = ApplicationService(settings), Overflow()
    worker = Worker(settings, provider)
    service.submit_sync(
        SyncRequest(mode="namechange", history_refresh="full", end_date="2024-06-28")
    )
    worker.run_once()
    provider.calls.clear()
    job = service.submit_sync(SyncRequest(mode="namechange", end_date="2024-06-29"))
    worker.run_once()
    assert service.job(job["id"])["status"] == "succeeded"
    assert any(d == "namechange" and p.get("ts_code") for d, p in provider.calls)
    assert any(
        d == "namechange" and p.get("start_date") == p.get("end_date") for d, p in provider.calls
    )


def test_cancelled_refresh_does_not_commit_observation(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path, database_path=tmp_path / "db")
    service, provider = ApplicationService(settings), ChangedProvider()
    worker = Worker(settings, provider)
    service.submit_sync(SyncRequest(mode="dividend", history_refresh="full", end_date="2024-06-28"))
    worker.run_once()
    job = service.submit_sync(SyncRequest(mode="dividend", end_date="2024-07-01"))
    publish = worker.repository.publish

    def cancel(*args: object, **kwargs: object) -> str:
        version = publish(*args, **kwargs)
        if args[0] == "dividend":
            service.cancel(job["id"], "sync")
        return version

    with patch.object(worker.repository, "publish", side_effect=cancel):
        worker.run_once()
    assert service.job(job["id"])["status"] == "cancelled"
    assert (
        service.repository.coverage("dividend", service.db.snapshot())["history:observation"][
            "as_of_date"
        ]
        == "2024-06-28"
    )
    service.resume(job["id"])
    worker.run_once()
    assert service.job(job["id"])["status"] == "succeeded"


def test_monthly_baseline_reuse_and_daily_observation_does_not_skip_next_month(
    tmp_path: Path,
) -> None:
    from datetime import datetime

    from ashare_lab.scheduler import TZ, DailyNameScheduler, MonthlyDividendScheduler

    settings = Settings(
        _env_file=None, data_dir=tmp_path, database_path=tmp_path / "db", tushare_token="test-only"
    )
    service = ApplicationService(settings)
    code = "000001.SZ"
    service.repository.publish(
        "stock_basic",
        normalize(
            "stock_basic",
            MiniProvider().fetch("stock_basic", {"list_status": "L"}),
            "tushare",
            "seed",
        ),
        "L",
        "tushare",
        "seed",
    )
    service.repository.publish(
        "dividend",
        pl.DataFrame(),
        f"stock:{code}",
        "tushare",
        "seed",
        coverage_info={"as_of_date": "2026-10-08"},
    )
    monthly = MonthlyDividendScheduler(settings, service.db, service.repository)
    monthly.tick(datetime(2026, 10, 9, 6, tzinfo=TZ))
    assert not service.task_list("sync")
    service.repository.publish(
        "dividend",
        pl.DataFrame(),
        "history:observation",
        "tushare",
        "seed",
        coverage_info={
            "as_of_date": "2026-11-10",
            "full_as_of_date": "2026-10-08",
            "complete_codes": [code],
        },
    )
    monthly.tick(datetime(2026, 11, 10, 6, tzinfo=TZ))
    job = service.task_list("sync")[0]
    assert job["payload"] == {
        "mode": "dividend",
        "end_date": "2026-11-06",
        "history_refresh": "full",
    }
    daily = DailyNameScheduler(settings, service.db, service.repository)
    daily.tick(datetime(2026, 11, 10, 2, tzinfo=TZ))
    assert service.task_list("sync")[0]["payload"] == {
        "mode": "namechange",
        "end_date": "2026-11-09",
        "history_refresh": "incremental",
    }


def test_new_stock_missing_history_is_filled_without_refreshing_unchanged_stock(
    tmp_path: Path,
) -> None:
    from datetime import date

    from ashare_lab.history import HistoryRefresh

    class NewStock(MiniProvider):
        def fetch(self, dataset: str, params: dict[str, str]) -> pl.DataFrame:
            self.calls.append((dataset, params))
            if dataset == "dividend" and not params.get("ts_code"):
                return pl.DataFrame()
            frame = super().fetch(dataset, params)
            if dataset == "dividend" and frame.height:
                return frame.with_columns(pl.lit(params["ts_code"]).alias("ts_code"))
            return frame

    service = ApplicationService(
        Settings(_env_file=None, data_dir=tmp_path, database_path=tmp_path / "db")
    )
    provider = NewStock()
    seed = normalize(
        "dividend", provider.fetch("dividend", {"ts_code": "000001.SZ"}), "fixture", "seed"
    )
    service.repository.publish(
        "dividend",
        seed,
        "stock:000001.SZ",
        "fixture",
        "seed",
        coverage_info={"as_of_date": "2025-07-01"},
    )
    provider.calls.clear()
    job = service.submit_sync(SyncRequest(mode="dividend", end_date="2025-07-02"))
    result = HistoryRefresh(service.db, service.repository, provider).run(
        job, "dividend", ["000001.SZ", "000002.SZ"], date(2025, 7, 2), False, lambda: None
    )
    assert result["refreshed_count"] == 1
    assert {p["ts_code"] for d, p in provider.calls if d == "dividend" and p.get("ts_code")} == {
        "000002.SZ"
    }
    assert service.repository.history_complete_codes(
        "dividend", "2025-07-02", service.db.snapshot()
    ) == {"000001.SZ", "000002.SZ"}


def test_management_and_access_token_remain_independent(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        database_path=tmp_path / "db",
        api_access_token="test-access",
        data_management_password="test-management",
    )
    client = TestClient(create_app(settings))
    assert client.get("/api/v1/data/status").status_code == 401
    assert (
        client.get(
            "/api/v1/data/status", headers={"Authorization": "Bearer test-access"}
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/api/v1/data/status",
            headers={
                "Authorization": "Bearer test-access",
                "X-Data-Management-Password": "test-management",
            },
        ).status_code
        == 200
    )
    assert (
        client.get("/api/v1/market/status", headers={"Authorization": "Bearer test-access"}).json()[
            "recent_sync"
        ]
        == []
    )
