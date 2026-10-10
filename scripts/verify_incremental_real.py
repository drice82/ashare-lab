"""Verify scheduled incremental sync with real requests in a temporary warehouse.

Restricts all stock requests to 000001.SZ; never writes production market data.
This checks the task path, not full-market completion or 16:00 data availability.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

import polars as pl
from ashare_lab.config import Settings
from ashare_lab.providers import TushareProvider, normalize
from ashare_lab.scheduler import DailyIncrementalScheduler
from ashare_lab.services import ApplicationService
from ashare_lab.worker import Worker

CODE = "000001.SZ"
TZ = ZoneInfo("Asia/Shanghai")


class BoundedProvider(TushareProvider):
    def __init__(self, settings: Settings):
        super().__init__(settings)
        self.calls: list[str] = []

    def fetch(self, dataset: str, params: dict[str, str]) -> pl.DataFrame:
        assert dataset not in {"dividend", "namechange"}
        self.calls.append(dataset)
        if dataset != "trade_cal":
            params = {**params, "ts_code": CODE}
        return super().fetch(dataset, params)


def main() -> int:
    original = Settings(_env_file=None)
    if not original.tushare_token.get_secret_value():
        print("未配置环境变量 TUSHARE_TOKEN：真实增量验证未执行。")
        return 2
    instant = datetime.now(TZ)
    provider = BoundedProvider(original)
    with TemporaryDirectory(prefix="ashare-incremental-real-") as directory:
        settings = Settings(
            _env_file=None,
            **{
                **original.model_dump(),
                "data_dir": Path(directory),
                "database_path": Path(directory) / "business.db",
            },
        )
        service = ApplicationService(settings)
        scheduler = DailyIncrementalScheduler(settings, service.db, service.repository)
        end = scheduler.due(instant).date()
        calendar = provider.fetch(
            "trade_cal",
            {
                "exchange": "SSE",
                "start_date": (end - timedelta(days=20)).strftime("%Y%m%d"),
                "end_date": end.strftime("%Y%m%d"),
            },
        )
        days = sorted(str(r["cal_date"]) for r in calendar.to_dicts() if int(r["is_open"]) == 1)
        assert len(days) >= 2
        raw = provider.fetch("daily", {"trade_date": days[-2]})
        assert raw.height > 0
        service.repository.publish(
            "daily",
            normalize("daily", raw, "tushare", "real-seed"),
            days[-2],
            "tushare",
            "real-seed",
        )
        old = service.db.snapshot()
        old_rows = service.repository.read("daily", old).to_dicts()
        provider.calls.clear()
        scheduler.tick(instant)
        job = service.task_list("sync")[0]
        Worker(settings, provider).run_once()
        outcome = service.job(job["id"])
        report = {
            "scope": CODE,
            "isolated_temporary_warehouse": True,
            "status": outcome["status"],
            "end_date": job["payload"]["end_date"],
            "datasets_requested": sorted(set(provider.calls)),
        }
        if outcome["status"] != "succeeded":
            print(json.dumps(report, ensure_ascii=False))
            return 1
        assert (
            service.repository.latest_date("daily", service.db.snapshot()).replace("-", "")
            == days[-1]
        )
        assert service.repository.read("daily", old).to_dicts() == old_rows
        assert {
            row["stock_code"]
            for row in service.repository.read("daily", service.db.snapshot()).to_dicts()
        } == {CODE}
        scheduler.tick(instant)
        assert len(service.task_list("sync")) == 1
        report.update(
            persistent_dedup="passed", old_version_read="passed", latest_trade_date=days[-1]
        )
        print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
