"""Bounded real-data acceptance in a temporary warehouse, isolated from user data.

This validates one stock across provider -> normalization -> warehouse -> factors ->
API/Worker, without claiming full-market SyncService throughput or coverage.
"""

import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

import polars as pl
from ashare_lab.api import create_app
from ashare_lab.config import Settings
from ashare_lab.providers import LIMITS, ProviderError, TushareProvider, normalize
from ashare_lab.rules import DEFAULT_F
from ashare_lab.worker import Worker
from fastapi.testclient import TestClient


def main() -> int:
    original = Settings()
    if not original.tushare_token.get_secret_value():
        print("未配置 TUSHARE_TOKEN：真实端到端验证未执行。")
        return 2
    provider = TushareProvider(original)
    end = datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)
    start = end - timedelta(days=150)
    dates = {"start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")}
    code = "000001.SZ"
    report: dict = {
        "scope": code,
        "isolated_temporary_warehouse": True,
        "datasets": {},
        "rules": {},
    }
    with TemporaryDirectory(prefix="ashare-live-") as directory:
        settings = Settings(
            **{
                **original.model_dump(),
                "data_dir": Path(directory),
                "database_path": Path(directory) / "business.db",
            }
        )
        app = create_app(settings)
        service = app.state.service
        frames: dict[str, pl.DataFrame] = {}
        for dataset in [
            "trade_cal",
            "stock_basic",
            "namechange",
            "daily",
            "adj_factor",
            "daily_basic",
            "dividend",
            "stk_limit",
            "suspend_d",
        ]:
            params = {"exchange": "SSE", **dates} if dataset == "trade_cal" else {"ts_code": code}
            if dataset in {"daily", "adj_factor", "daily_basic", "stk_limit"}:
                params.update(dates)
            if dataset == "suspend_d":
                params["trade_date"] = end.strftime("%Y%m%d")
            try:
                raw = provider.fetch(dataset, params)
                if raw.height >= LIMITS[dataset]:
                    raise ProviderError("truncated", f"{dataset} 达到上限")
                frames[dataset] = normalize(dataset, raw, "tushare", "live-acceptance")
                report["datasets"][dataset] = {"rows": raw.height, "status": "normalized"}
            except ProviderError as exc:
                report["datasets"][dataset] = {"status": "failed", "code": exc.code}
                print(json.dumps(report, ensure_ascii=False, indent=2))
                return 1
        days = sorted(frames["trade_cal"].filter(pl.col("is_open") == 1)["cal_date"].to_list())
        as_of = days[-1]
        report["as_of_date"] = as_of
        # The requested calendar is real; no synthetic dates or prices are introduced.
        expected_days = {
            (start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)
        }
        assert set(frames["trade_cal"]["cal_date"].to_list()) == expected_days
        for dataset, frame in frames.items():
            if "stock_code" in frame.columns:
                assert not frame.filter(pl.col("stock_code") != code).height
            coverage = (
                {"trade_dates": days}
                if dataset in {"daily", "adj_factor", "daily_basic", "stk_limit"}
                else {}
            )
            key = f"stock:{code}" if dataset in {"namechange", "dividend"} else "live-acceptance"
            if dataset in {"namechange", "dividend"}:
                coverage = {"as_of_date": as_of}
            if dataset == "suspend_d":
                coverage = {"trade_date": end.isoformat()}
            service.repository.publish(
                dataset, frame, key, "tushare", "live-acceptance", coverage_info=coverage
            )
        bars = (
            frames["daily"]
            .join(
                frames["adj_factor"].select("stock_code", "trade_date", "adj_factor"),
                on=["stock_code", "trade_date"],
            )
            .filter(pl.col("trade_date") <= as_of)
            .sort("trade_date")
        )
        assert bars["trade_date"][-1] == as_of and bars.height >= 61
        assert bars["trade_date"].tail(61).to_list() == days[-61:]
        anchor = bars["adj_factor"][-1]
        adjusted = [row["close"] * row["adj_factor"] / anchor for row in bars.to_dicts()]
        manual_ma20 = sum(adjusted[-20:]) / 20
        manual_ma60 = sum(adjusted[-60:]) / 60
        manual_volume_ratio = bars["volume"][-1] / (sum(bars["volume"].to_list()[-6:-1]) / 5)
        worker = Worker(settings)
        headers = (
            {"Authorization": f"Bearer {settings.api_access_token.get_secret_value()}"}
            if settings.api_access_token.get_secret_value()
            else {}
        )
        with TestClient(app, headers=headers) as client:
            for rule in "ABCDEF":
                request = {
                    "as_of_date": as_of,
                    "rule_id": rule,
                    "universe": {"type": "custom", "codes": [code]},
                    "parameters": DEFAULT_F if rule == "F" else {},
                }
                submitted = client.post("/api/v1/screening/run", json=request)
                assert submitted.status_code == 202, submitted.text
                job_id = submitted.json()["id"]
                assert worker.run_once()
                job = client.get(f"/api/v1/screening/runs/{job_id}").json()
                assert job["status"] == "succeeded", job
                result = client.get(
                    f"/api/v1/screening/runs/{job_id}/results?match_status=all"
                ).json()
                assert result["total"] == 1
                row = result["items"][0]
                value = row["factor_values"]
                for field, expected in [
                    ("ma20", manual_ma20),
                    ("ma60", manual_ma60),
                    ("volume_ratio_5d", manual_volume_ratio),
                ]:
                    assert math.isclose(value[field], expected, rel_tol=1e-10), (
                        field,
                        value[field],
                        expected,
                    )
                assert client.get(f"/api/v1/screening/runs/{job_id}/export").status_code == 200
                chart = client.get(
                    "/api/v1/market/bars", params={"stock_code": code, "run_id": job_id}
                )
                assert chart.status_code == 200 and chart.json()["items"][-1]["trade_date"] == as_of
                report["rules"][rule] = {
                    "task_status": job["status"],
                    "match_status": row["match_status"],
                    "factor_values": value,
                    "quality_flags": row["data_quality_flags"],
                }
        report["manual_indicator_checks"] = "MA20, MA60 and prior-five-day volume ratio passed"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
