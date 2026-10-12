from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest
from ashare_lab.config import Settings
from ashare_lab.providers import ProviderError
from ashare_lab.schemas import SyncRequest
from ashare_lab.services import ApplicationService
from ashare_lab.worker import Worker
from test_sync_recovery import MiniProvider


class EarlyCalendarProvider(MiniProvider):
    fail_day = False
    invalid_flag: float | None = 0

    def fetch(self, dataset: str, params: dict[str, str]) -> pl.DataFrame:
        if dataset == "trade_cal":
            self.calls.append((dataset, params))
            first = max(
                date(1990, 12, 19),
                date.fromisoformat(
                    f"{params['start_date'][:4]}-{params['start_date'][4:6]}-{params['start_date'][6:]}"
                ),
            )
            last = date.fromisoformat(
                f"{params['end_date'][:4]}-{params['end_date'][4:6]}-{params['end_date'][6:]}"
            )
            days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
            return pl.DataFrame(
                {
                    "exchange": ["SSE"] * len(days),
                    "cal_date": [d.isoformat() for d in days],
                    "is_open": [1 if d == first else self.invalid_flag for d in days],
                }
            )
        if params.get("trade_date"):
            if self.fail_day:
                raise ProviderError("temporary_error", "中断测试")
            mapped = dict(params, trade_date="20240627")
            frame = super().fetch(dataset, mapped)
            return (
                frame.with_columns(pl.lit("1990-12-19").alias("trade_date"))
                if frame.height
                else frame
            )
        return super().fetch(dataset, params)


def setup_service(tmp_path: Path) -> tuple[ApplicationService, EarlyCalendarProvider, Worker]:
    settings = Settings(_env_file=None, data_dir=tmp_path, database_path=tmp_path / "business.db")
    provider = EarlyCalendarProvider()
    return ApplicationService(settings), provider, Worker(settings, provider)


def test_early_calendar_resume_repeat_and_old_snapshot(tmp_path: Path) -> None:
    service, provider, worker = setup_service(tmp_path)
    request = SyncRequest(mode="initialize", start_date="1990-11-02", end_date="1990-12-20")
    job = service.submit_sync(request)
    provider.fail_day = True
    worker.run_once()
    assert service.job(job["id"])["status"] == "failed"
    calendar_calls = [p for d, p in provider.calls if d == "trade_cal"]
    assert calendar_calls == [{"exchange": "SSE", "start_date": "19901219", "end_date": "19901220"}]
    snapshot = service.db.snapshot()
    rows = service.repository.read("trade_cal", snapshot).to_dicts()
    assert [r["cal_date"] for r in rows] == ["1990-12-19", "1990-12-20"]
    provider.fail_day = False
    provider.calls.clear()
    service.resume(job["id"])
    worker.run_once()
    outcome = service.job(job["id"])
    assert outcome["status"] == "partial"
    assert outcome["result"]["start_date"] == "1990-12-19"
    assert "1990-12-19" in outcome["result"]["warnings"][0]
    assert not any(d == "trade_cal" for d, _ in provider.calls)
    service.submit_sync(request)
    worker.run_once()
    assert service.repository.read("trade_cal", snapshot).to_dicts() == rows
    assert service.repository.read("trade_cal", service.db.snapshot()).height == 2


def test_range_before_supported_calendar_fails_clearly(tmp_path: Path) -> None:
    service, provider, worker = setup_service(tmp_path)
    job = service.submit_sync(
        SyncRequest(mode="initialize", start_date="1990-11-02", end_date="1990-12-18")
    )
    worker.run_once()
    outcome = service.job(job["id"])
    assert outcome["status"] == "failed"
    assert "1990-12-19" in outcome["error"]
    assert not provider.calls


@pytest.mark.parametrize("flag", [None, 2, 0.5])
def test_invalid_calendar_flags_are_never_published(tmp_path: Path, flag: float | None) -> None:
    service, provider, worker = setup_service(tmp_path)
    provider.invalid_flag = flag
    job = service.submit_sync(
        SyncRequest(mode="initialize", start_date="1990-12-19", end_date="1990-12-20")
    )
    worker.run_once()
    assert service.job(job["id"])["status"] == "failed"
    assert "trade_cal" not in service.db.snapshot()
