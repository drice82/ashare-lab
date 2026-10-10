"""Run DCA against a read-only snapshot of an existing real warehouse.

No provider calls, production jobs, credentials, or market files are modified.
The copied SQLite catalog and generated results live in a temporary directory.
"""

import argparse
import json
import math
import sqlite3
import sys
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from ashare_lab.api import create_app
from ashare_lab.config import Settings
from ashare_lab.worker import Worker
from fastapi.testclient import TestClient


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--stock-code", default="000001.SZ")
    args = parser.parse_args()
    source = args.data_dir.resolve()
    with TemporaryDirectory(prefix="ashare-dca-real-") as temporary:
        root = Path(temporary)
        with sqlite3.connect(f"file:{source / 'business.db'}?mode=ro", uri=True) as original:
            with sqlite3.connect(root / "business.db") as copied:
                original.backup(copied)
                # Only clear the isolated copy so its Worker cannot resume production jobs.
                copied.execute("DELETE FROM sync_checkpoints")
                copied.execute("DELETE FROM job_logs")
                copied.execute("DELETE FROM jobs")
                copied.commit()
        (root / "market").symlink_to(source / "market", target_is_directory=True)
        settings = Settings(_env_file=None, data_dir=root, database_path=root / "business.db")
        app = create_app(settings)
        service = app.state.service
        snapshot = service.db.snapshot()
        sources = {
            k: service.repository.version(v, resolve_coverage=False)["source"]
            for k, v in snapshot.items()
        }
        if "fixture" in sources.values():
            print("真实验收未执行：来源包含 fixture。")
            return 2
        latest = service.repository.latest_date("daily", snapshot)
        if latest is None:
            print("真实验收未完成：没有已发布日线。")
            return 2
        start = (date.fromisoformat(latest) - timedelta(days=60)).isoformat()
        bars = service.repository.read(
            "daily", snapshot, [args.stock_code], start, latest
        ).to_dicts()
        days = sorted(r["trade_date"] for r in bars)
        if not days:
            print("真实验收未完成：所选股票无区间行情。")
            return 2
        client = TestClient(app)
        body = {
            "stock_code": args.stock_code,
            "start_date": days[0],
            "end_date": days[-1],
            "monthly_day": date.fromisoformat(days[0]).day,
            "monthly_amount": "3000",
            "data_versions": snapshot,
        }
        submitted = client.post("/api/v1/dca/run", json=body)
        if submitted.status_code != 202:
            print(
                json.dumps(
                    {"scope": args.stock_code, "submission": submitted.json()}, ensure_ascii=False
                )
            )
            return 2
        job_id = submitted.json()["id"]
        assert Worker(settings).run_once()
        job = client.get(f"/api/v1/dca/runs/{job_id}").json()
        if job["status"] != "succeeded":
            print(
                json.dumps(
                    {"scope": args.stock_code, "status": job["status"], "error": job["error"]},
                    ensure_ascii=False,
                )
            )
            return 2
        result = service.dca.result(job_id)
        assert result["fixture_mode"] is False
        raw_prices = {r["trade_date"]: Decimal(str(r["close"])) for r in bars}
        cash, shares = Decimal(0), 0
        for row in result["ledger"]:
            if row["event"] in {"contribution", "dividend"}:
                cash += Decimal(str(row["amount"]))
            if row["event"] == "buy":
                assert Decimal(str(row["price"])) == raw_prices[row["date"]]
                notional = raw_prices[row["date"]] * row["shares_delta"]
                expected_fee = max(Decimal(5), notional * Decimal("0.0003")).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                expected_fee += (notional * Decimal("0.00001")).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                assert Decimal(str(row["fee"])) == expected_fee
                cash -= notional + expected_fee
                shares += row["shares_delta"]
            assert Decimal(str(row["cash"])) == cash
            assert row["shares"] == shares
        summary = result["summary"]
        if summary["complete"]:
            rate = summary["annualized_return"]
            if rate is not None:
                terminal = date.fromisoformat(body["end_date"])
                accumulated = sum(
                    row["amount"]
                    * (1 + rate) ** ((terminal - date.fromisoformat(row["date"])).days / 365)
                    for row in result["ledger"]
                    if row["event"] == "contribution"
                )
                assert math.isclose(accumulated, summary["total_assets"], rel_tol=1e-9)
            # Independent share-issuance accounting using ledger contributions.
            flows: dict[str, float] = {}
            for row in result["ledger"]:
                if row["event"] == "contribution":
                    flows[row["date"]] = flows.get(row["date"], 0) + row["amount"]
            units, nav, peak, drawdown = 0.0, 1.0, 1.0, 0.0
            for point in result["curve"]:
                units += flows.get(point["date"], 0) / nav
                if units:
                    nav = point["total_assets"] / units
                    peak = max(peak, nav)
                    drawdown = max(drawdown, (peak - nav) / peak)
            assert math.isclose(drawdown, summary["max_drawdown"], abs_tol=1e-10)
        assert client.get(f"/api/v1/dca/runs/{job_id}/export").status_code == 200
        report = {
            "scope": args.stock_code,
            "start_date": days[0],
            "end_date": days[-1],
            "fixture_mode": False,
            "isolated_snapshot": True,
            "sources": sources,
            "data_versions": snapshot,
            "summary": result["summary"],
            "warnings": result["warnings"],
            "ledger_rows": len(result["ledger"]),
            "cash_and_fees_verified": True,
            "performance_metrics_verified": summary["complete"],
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if result["summary"]["complete"] else 2


if __name__ == "__main__":
    sys.exit(main())
