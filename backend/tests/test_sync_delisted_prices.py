from pathlib import Path

import polars as pl
import pytest
from ashare_lab.config import Settings
from ashare_lab.schemas import SyncRequest
from ashare_lab.services import ApplicationService
from ashare_lab.worker import Worker
from test_sync_recovery import MiniProvider


class DelistedPriceProvider(MiniProvider):
    bad_survivor = False

    def fetch(self, dataset: str, params: dict[str, str]) -> pl.DataFrame:
        if dataset == "stock_basic" and params.get("list_status") == "D":
            return pl.DataFrame(
                [
                    {
                        "ts_code": "000003.SZ",
                        "exchange": "SZSE",
                        "list_status": "D",
                        "list_date": "19910703",
                        "delist_date": "20020614",
                        "name": "历史退市证券",
                    }
                ]
            )
        frame = super().fetch(dataset, params)
        if dataset == "daily" and frame.height:
            bad = frame.with_columns(
                pl.lit("000001.SZ" if self.bad_survivor else "000003.SZ").alias("ts_code"),
                pl.lit(15.5).alias("open"),
                pl.lit(18.3).alias("high"),
                pl.lit(17.5).alias("low"),
                pl.lit(17.7).alias("close"),
            )
            return bad if self.bad_survivor else pl.concat([frame, bad])
        return frame


@pytest.mark.parametrize("legacy,bad_survivor", [(False, False), (True, False), (True, True)])
def test_delisted_filter_preserves_strict_survivor_and_legacy_validation(
    tmp_path: Path,
    legacy: bool,
    bad_survivor: bool,
) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path, database_path=tmp_path / "business.db")
    service = ApplicationService(settings)
    provider = DelistedPriceProvider()
    provider.bad_survivor = bad_survivor
    worker = Worker(settings, provider)
    request = SyncRequest(mode="initialize", start_date="2024-06-27", end_date="2024-06-28")
    job = service.submit_sync(request)
    if legacy:
        snapshot = dict(job["snapshot"])
        snapshot.pop("_sync_policy")
        service.db.update(job["id"], snapshot=snapshot)
    if not legacy and not bad_survivor:
        provider.fail = True
    worker.run_once()
    outcome = service.job(job["id"])
    if legacy or bad_survivor:
        assert outcome["status"] == "failed"
        assert "OHLC" in outcome["error"]
        assert ("000001.SZ" if bad_survivor else "000003.SZ") in outcome["error"]
        assert "2024-06-27" in outcome["error"]
        assert "15.5" in outcome["error"]
        assert "daily" not in service.db.snapshot()
        return
    assert outcome["status"] == "failed"
    interrupted_snapshot = service.db.snapshot()
    interrupted_rows = service.repository.read("daily", interrupted_snapshot).to_dicts()
    assert len(interrupted_rows) == 1
    provider.fail = False
    provider.calls.clear()
    service.resume(job["id"])
    worker.run_once()
    assert not any(
        dataset == "daily" and params.get("trade_date") == "20240627"
        for dataset, params in provider.calls
    )
    assert service.repository.read("daily", interrupted_snapshot).to_dicts() == interrupted_rows
    outcome = service.job(job["id"])
    assert outcome["status"] == "succeeded"
    old = service.db.snapshot()
    old_rows = service.repository.read("daily", old).to_dicts()
    assert {r["stock_code"] for r in old_rows} == {"000001.SZ"}
    service.submit_sync(request)
    worker.run_once()
    assert service.repository.read("daily", old).to_dicts() == old_rows
    assert service.repository.read("daily", service.db.snapshot()).height == 2


def test_survivor_filter_accepts_day_with_only_delisted_rows(tmp_path: Path) -> None:
    class OnlyDelistedProvider(DelistedPriceProvider):
        def fetch(self, dataset: str, params: dict[str, str]) -> pl.DataFrame:
            frame = super().fetch(dataset, params)
            return frame.filter(pl.col("ts_code") == "000003.SZ") if dataset == "daily" else frame

    settings = Settings(_env_file=None, data_dir=tmp_path, database_path=tmp_path / "business.db")
    service = ApplicationService(settings)
    worker = Worker(settings, OnlyDelistedProvider())
    job = service.submit_sync(
        SyncRequest(mode="initialize", start_date="2024-06-27", end_date="2024-06-28")
    )
    worker.run_once()
    assert service.job(job["id"])["status"] == "succeeded"
    assert service.repository.read("daily", service.db.snapshot()).is_empty()
    assert service.repository.covered_date("daily", "2024-06-27", service.db.snapshot())


@pytest.mark.parametrize("code", [None, "INVALID.SH", 123])
def test_delisted_filter_keeps_unknown_or_missing_codes_for_validation(
    tmp_path: Path,
    code: str | int | None,
) -> None:
    class MalformedProvider(DelistedPriceProvider):
        def fetch(self, dataset: str, params: dict[str, str]) -> pl.DataFrame:
            frame = super().fetch(dataset, params)
            if dataset == "daily":
                return frame.with_columns(pl.lit(code).alias("ts_code"))
            return frame

    settings = Settings(_env_file=None, data_dir=tmp_path, database_path=tmp_path / "business.db")
    service = ApplicationService(settings)
    job = service.submit_sync(
        SyncRequest(mode="initialize", start_date="2024-06-27", end_date="2024-06-28")
    )
    Worker(settings, MalformedProvider()).run_once()
    assert service.job(job["id"])["status"] == "failed"
    assert "股票代码" in service.job(job["id"])["error"]
    assert "daily" not in service.db.snapshot()
