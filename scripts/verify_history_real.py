"""Real date discovery versus full histories; temporary warehouse, two-stock scope.

Read TUSHARE_TOKEN from environment only. Report no credentials or market records.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

import polars as pl
from ashare_lab.config import Settings
from ashare_lab.history import HistoryRefresh
from ashare_lab.providers import ProviderError, TushareProvider, normalize
from ashare_lab.schemas import SyncRequest
from ashare_lab.services import ApplicationService
from ashare_lab.warehouse import KEYS, PROVENANCE

CODES = ["000001.SZ", "600036.SH"]


def main() -> int:
    settings = Settings(_env_file=None)
    if not settings.tushare_token.get_secret_value():
        print("未配置 TUSHARE_TOKEN 环境变量，真实验证未执行。")
        return 2
    provider = TushareProvider(settings)
    end = datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)
    report = {"scope": CODES, "temporary_warehouse": True, "datasets": {}}
    try:
        with TemporaryDirectory(prefix="ashare-history-real-") as directory:
            isolated = settings.model_copy(
                update={"data_dir": Path(directory), "database_path": Path(directory) / "db"}
            )
            service = ApplicationService(isolated)
            for dataset in ["namechange", "dividend"]:
                for code in CODES:
                    raw = provider.fetch(dataset, {"ts_code": code})
                    service.repository.publish(
                        dataset,
                        normalize(dataset, raw, "tushare", "seed"),
                        f"stock:{code}",
                        "tushare",
                        "seed",
                        coverage_info={"as_of_date": (end - timedelta(days=1)).isoformat()},
                    )
                old = service.db.snapshot()
                old_rows = service.repository.read(dataset, old).to_dicts()
                job = service.submit_sync(SyncRequest(mode=dataset, end_date=end))
                result = HistoryRefresh(service.db, service.repository, provider).run(
                    job, dataset, CODES, end, False, lambda: None
                )
                actual = service.repository.read(dataset, service.db.snapshot())
                assert service.repository.read(dataset, old).to_dicts() == old_rows
                expected = []
                for code in CODES:
                    raw = provider.fetch(dataset, {"ts_code": code})
                    expected.extend(normalize(dataset, raw, "tushare", "comparison").to_dicts())

                def comparable(rows: list[dict]) -> list[str]:
                    return sorted(
                        json.dumps(
                            {k: v for k, v in row.items() if k not in PROVENANCE},
                            sort_keys=True,
                            ensure_ascii=False,
                        )
                        for row in rows
                    )

                full_frame = pl.DataFrame(expected).unique(
                    subset=KEYS[dataset], keep="last", maintain_order=True
                )
                assert comparable(actual.to_dicts()) == comparable(full_frame.to_dicts())
                report["datasets"][dataset] = {
                    "matched_full_history": True,
                    "rows": actual.height,
                    "raw_full_rows": len(expected),
                    "duplicate_keys": len(expected) - full_frame.height,
                    "refreshed_count": result["refreshed_count"],
                    "scan_start": result["scan_start"],
                    "end_date": result["end_date"],
                }
                print(
                    json.dumps(
                        {"dataset": dataset, "matched_full_history": True}, ensure_ascii=False
                    ),
                    flush=True,
                )
        report["passed"] = True
    except AssertionError:
        report.update(
            passed=False,
            error={"code": "comparison_mismatch", "message": "增量与全量标准化记录不一致"},
        )
    except ProviderError as exc:
        report.update(passed=False, error={"code": exc.code, "message": str(exc)})
    Path("test-results").mkdir(exist_ok=True)
    Path("test-results/history-real.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
