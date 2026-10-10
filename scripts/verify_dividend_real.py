"""Bounded dividend Worker acceptance using real requests and a temporary warehouse.

The provider explicitly restricts stock_basic to one real stock. This verifies the
new task end to end, not full-market coverage or automatic scheduling in production.
Secrets are read from environment variables only and never included in the report.
"""

import json
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

import polars as pl
from ashare_lab.config import Settings
from ashare_lab.providers import TushareProvider
from ashare_lab.schemas import SyncRequest
from ashare_lab.services import ApplicationService
from ashare_lab.worker import Worker

CODE = "600339.SH"


class BoundedProvider(TushareProvider):
    def fetch(self, dataset: str, params: dict[str, str]) -> pl.DataFrame:
        assert dataset in {"stock_basic", "dividend"}
        return super().fetch(dataset, {**params, "ts_code": CODE})


def main() -> int:
    original = Settings(_env_file=None)
    if not original.tushare_token.get_secret_value():
        print("未配置环境变量 TUSHARE_TOKEN：真实分红验证未执行。")
        return 2
    with TemporaryDirectory(prefix="ashare-dividend-real-") as directory:
        settings = Settings(
            _env_file=None,
            **{
                **original.model_dump(),
                "data_dir": Path(directory),
                "database_path": Path(directory) / "business.db",
            },
        )
        service = ApplicationService(settings)
        provider = BoundedProvider(settings)
        worker = Worker(settings, provider)
        end = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        job = service.submit_sync(SyncRequest(mode="dividend", end_date=end))
        worker.run_once()
        outcome = service.job(job["id"])
        report = {"scope": CODE, "isolated_temporary_warehouse": True, "status": outcome["status"]}
        if outcome["status"] != "succeeded":
            print(json.dumps(report, ensure_ascii=False))
            return 1
        assert outcome["result"]["stock_count"] == 1
        old = service.db.snapshot()
        rows = service.repository.read("dividend", old).to_dicts()
        assert rows and {row["stock_code"] for row in rows} == {CODE}
        assert "daily" not in old and "namechange" not in old
        again = service.submit_sync(SyncRequest(mode="dividend", end_date=end))
        worker.run_once()
        assert service.job(again["id"])["status"] == "succeeded"
        assert service.repository.read("dividend", service.db.snapshot()).height == len(rows)
        assert service.repository.read("dividend", old).to_dicts() == rows
        assert service.repository.covered_stock("dividend", CODE, service.db.snapshot())
        report.update(
            rows=len(rows),
            duplicate_sync="passed",
            old_version_read="passed",
            no_market_requests="passed",
        )
        print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
