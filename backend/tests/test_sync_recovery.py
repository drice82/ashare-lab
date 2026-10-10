from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import polars as pl
import pytest
from ashare_lab.config import Settings
from ashare_lab.db import Database
from ashare_lab.fixtures import fixture_frames
from ashare_lab.providers import FIELDS, ProviderError
from ashare_lab.schemas import SyncRequest
from ashare_lab.services import ApplicationService
from ashare_lab.worker import Worker


def test_bounded_prefetch_overlaps_requests() -> None:
    import threading

    from ashare_lab.sync import prefetch

    barrier = threading.Barrier(2)
    active = 0
    peak = 0
    lock = threading.Lock()

    def request(value: int) -> int:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        barrier.wait(timeout=3)
        with lock:
            active -= 1
        return value * 2

    results = list(prefetch(request, range(6), 2))
    assert [(item, future.result()) for item, future in results] == [(i, i * 2) for i in range(6)]
    assert peak == 2


def test_prefetch_failure_is_delivered_to_publisher() -> None:
    from ashare_lab.sync import prefetch

    def request(value: int) -> int:
        if value == 1:
            raise ProviderError("temporary_error", "request failed")
        return value

    results = list(prefetch(request, range(3), 2))
    assert results[0][1].result() == 0
    with pytest.raises(ProviderError):
        results[1][1].result()
    assert results[2][1].result() == 2


def test_parallel_history_cancel_resume_retains_old_version(tmp_path: Path) -> None:
    from ashare_lab.providers import LIMITS

    class HistoryProvider(MiniProvider):
        def fetch(self, dataset: str, params: dict[str, str]) -> pl.DataFrame:
            if dataset == "namechange" and not params:
                return pl.DataFrame({"ts_code": ["000001.SZ"] * LIMITS["namechange"]})
            frame = super().fetch(dataset, params)
            if dataset == "stock_basic" and frame.height:
                return pl.concat([frame, frame.with_columns(pl.lit("000002.SZ").alias("ts_code"))])
            if params.get("ts_code") and "ts_code" in frame.columns:
                frame = frame.with_columns(pl.lit(params["ts_code"]).alias("ts_code"))
            return frame

    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        database_path=tmp_path / "business.db",
        sync_concurrency=2,
    )
    service = ApplicationService(settings)
    provider = HistoryProvider()
    worker = Worker(settings, provider)
    job = service.submit_sync(SyncRequest(mode="namechange", end_date="2024-06-28"))
    publish = worker.repository.publish

    def interrupt(*args: Any, **kwargs: Any) -> str:
        version = publish(*args, **kwargs)
        if args[0] == "namechange":
            service.cancel(job["id"], "sync")
        return version

    with patch.object(worker.repository, "publish", side_effect=interrupt):
        worker.run_once()
    assert service.job(job["id"])["status"] == "cancelled"
    old_snapshot = service.db.snapshot()
    old_rows = service.repository.read("namechange", old_snapshot).to_dicts()
    pinned = service.job(job["id"])["snapshot"]["_namechange_codes"]
    basics = service.repository.read("stock_basic", service.db.snapshot())
    service.repository.publish(
        "stock_basic",
        basics.head(1).with_columns(pl.lit("000003.SZ").alias("stock_code")),
        "new-stock",
        "fixture",
        "external",
    )
    provider.calls.clear()
    service.resume(job["id"])
    worker.run_once()
    assert service.job(job["id"])["status"] == "succeeded"
    assert service.job(job["id"])["snapshot"]["_namechange_codes"] == pinned
    assert not any(d == "namechange" and p.get("ts_code") == "000003.SZ" for d, p in provider.calls)
    assert not any(d == "namechange" and p.get("ts_code") == "000001.SZ" for d, p in provider.calls)
    assert service.repository.read("namechange", old_snapshot).to_dicts() == old_rows
    current = service.repository.read("namechange", service.db.snapshot())
    assert set(current["stock_code"].to_list()) == {"000001.SZ", "000002.SZ"}
    count = current.height
    service.submit_sync(SyncRequest(mode="namechange", end_date="2024-06-28"))
    worker.run_once()
    assert (
        service.repository.read("namechange", service.db.snapshot())
        .filter(pl.col("stock_code").is_in(["000001.SZ", "000002.SZ"]))
        .height
        == count
    )


class MiniProvider:
    source = "fixture"

    def __init__(self, fail: bool = False, deny_optional: bool = False):
        self.frames = fixture_frames()
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.fail, self.deny_optional = fail, deny_optional

    def fetch(self, dataset: str, params: dict[str, str]) -> pl.DataFrame:
        self.calls.append((dataset, params))
        if self.fail and dataset == "daily" and params.get("trade_date") == "20240628":
            raise ProviderError("temporary_error", "模拟第二日请求失败")
        if self.deny_optional and dataset in {
            "stk_limit",
            "suspend_d",
            "dividend",
            "adj_factor",
            "daily_basic",
            "namechange",
        }:
            raise ProviderError("permission_denied", f"{dataset} 权限不足")
        frame = self.frames[dataset]
        if "stock_code" in frame.columns:
            frame = frame.filter(pl.col("stock_code") == "000001.SZ")
        if dataset == "stock_basic" and params.get("list_status") != "L":
            frame = frame.clear()
        if "trade_date" in frame.columns:
            day = params.get("trade_date", "")
            iso = f"{day[:4]}-{day[4:6]}-{day[6:]}"
            frame = frame.filter(pl.col("trade_date") == iso)
        if dataset == "trade_cal":
            first = datetime.strptime(params["start_date"], "%Y%m%d").date()
            last = datetime.strptime(params["end_date"], "%Y%m%d").date()
            days = [(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]
            frame = pl.DataFrame(
                {
                    "exchange": ["SSE"] * len(days),
                    "cal_date": days,
                    "is_open": [int(d in {"2024-06-27", "2024-06-28"}) for d in days],
                }
            )
        rows: list[dict[str, Any]] = []
        for row in frame.to_dicts():
            row["ts_code"] = row.get("stock_code")
            if dataset == "daily":
                row.update(
                    vol=row["volume"] / 100,
                    amount=row["amount"] / 1000,
                    pct_chg=row["daily_return"] * 100,
                )
            rows.append({k: row.get(k) for k in FIELDS[dataset].split(",")})
        return pl.DataFrame(rows) if rows else pl.DataFrame()


def test_market_sync_filters_out_of_scope_before_price_validation(tmp_path: Path) -> None:
    class MixedMarketProvider(MiniProvider):
        def fetch(self, dataset: str, params: dict[str, str]) -> pl.DataFrame:
            frame = super().fetch(dataset, params)
            if dataset == "daily" and frame.height:
                outside = frame.with_columns(
                    pl.lit("920090.BJ").alias("ts_code"),
                    pl.lit(None, dtype=pl.Float64).alias("pre_close"),
                )
                b_share = outside.with_columns(pl.lit("900901.SH").alias("ts_code"))
                return pl.concat([frame, outside, b_share])
            return frame

    settings = Settings(_env_file=None, data_dir=tmp_path, database_path=tmp_path / "business.db")
    service = ApplicationService(settings)
    worker = Worker(settings, MixedMarketProvider())
    job = service.submit_sync(
        SyncRequest(mode="initialize", start_date="2024-06-27", end_date="2024-06-28")
    )
    worker.run_once()
    assert service.job(job["id"])["status"] == "succeeded"
    bars = service.repository.read("daily", service.db.snapshot())
    assert bars["stock_code"].to_list() == ["000001.SZ", "000001.SZ"]
    assert bars["pre_close"].null_count() == 0


def test_real_sync_pipeline_resume_and_no_duplicates(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path, database_path=tmp_path / "business.db")
    service = ApplicationService(settings)
    provider = MiniProvider(fail=True)
    worker = Worker(settings, provider)
    job = service.submit_sync(
        SyncRequest(mode="initialize", start_date="2024-06-27", end_date="2024-06-28")
    )
    worker.run_once()
    assert service.job(job["id"])["status"] == "failed"
    assert service.repository.read("daily", service.db.snapshot()).height == 1
    provider.fail = False
    provider.calls.clear()
    service.resume(job["id"])
    worker.run_once()
    assert service.job(job["id"])["status"] == "succeeded"
    assert not any(
        dataset == "daily" and params.get("trade_date") == "20240627"
        for dataset, params in provider.calls
    )
    assert service.repository.read("daily", service.db.snapshot()).height == 2
    service.submit_sync(
        SyncRequest(mode="initialize", start_date="2024-06-27", end_date="2024-06-28")
    )
    worker.run_once()
    assert service.repository.read("daily", service.db.snapshot()).height == 2


def test_optional_permission_failure_is_partial_not_success(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path, database_path=tmp_path / "business.db")
    service = ApplicationService(settings)
    worker = Worker(settings, MiniProvider(deny_optional=True))
    job = service.submit_sync(
        SyncRequest(mode="initialize", start_date="2024-06-27", end_date="2024-06-28")
    )
    worker.run_once()
    outcome = service.job(job["id"])
    assert outcome["status"] == "partial"
    assert len(outcome["result"]["warnings"]) == 5
    assert "adj_factor" not in service.db.snapshot()


def test_failed_file_publication_keeps_old_manifest(tmp_path: Path) -> None:
    from ashare_lab.warehouse import MarketRepository

    db = Database(tmp_path / "business.db")
    db.initialize()
    repository = MarketRepository(db, tmp_path)
    frame = pl.DataFrame(
        {"stock_code": ["000001.SZ"], "trade_date": ["2024-06-27"], "close": [10.0]}
    )
    repository.publish("daily", frame, "day", "fixture", "batch")
    snapshot = db.snapshot()
    with (
        patch("ashare_lab.warehouse.os.replace", side_effect=OSError("disk failure")),
        pytest.raises(OSError),
    ):
        repository.publish(
            "daily", frame.with_columns(pl.lit(99.0).alias("close")), "day", "fixture", "batch2"
        )
    assert db.snapshot() == snapshot
    assert repository.read("daily", snapshot)["close"].item() == 10


def test_incomplete_calendar_is_rejected_without_publishing(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path, database_path=tmp_path / "business.db")
    service = ApplicationService(settings)
    provider = MiniProvider()
    original = provider.fetch

    def incomplete(dataset: str, params: dict[str, str]) -> pl.DataFrame:
        frame = original(dataset, params)
        return frame.slice(1) if dataset == "trade_cal" else frame

    with patch.object(provider, "fetch", side_effect=incomplete):
        job = service.submit_sync(
            SyncRequest(mode="initialize", start_date="2024-06-27", end_date="2024-06-28")
        )
        Worker(settings, provider).run_once()
    assert service.job(job["id"])["status"] == "failed"
    assert "trade_cal" not in service.db.snapshot()
    assert "daily" not in service.db.snapshot()
