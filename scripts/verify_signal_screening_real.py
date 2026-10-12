"""Pinned real-data API/Worker screening audit in an isolated SQLite copy.

Market files are read through a symlink; no provider requests, notifications,
production tasks or data publication. Directions use an independent NumPy oracle.
"""

import argparse
import csv
import io
import json
import sqlite3
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from ashare_lab.api import create_app
from ashare_lab.config import Settings
from ashare_lab.schemas import StrategyConfig
from ashare_lab.signals import warmup
from ashare_lab.worker import Worker
from fastapi.testclient import TestClient
from verify_comparison_real import CONFIGS, oracle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--trade-date", type=date.fromisoformat, required=True)
    parser.add_argument("--versions-file", type=Path, required=True)
    parser.add_argument("--stocks", nargs="+", default=["000001.SZ", "000338.SZ", "600519.SH"])
    args = parser.parse_args()
    source = args.data_dir.resolve()
    day = args.trade_date.isoformat()
    snapshot = json.loads(args.versions_file.read_text())
    assert {"stock_basic", "trade_cal", "daily", "adj_factor", "suspend_d"} <= snapshot.keys()
    assert all(not key.startswith("_") for key in snapshot)
    with TemporaryDirectory(prefix="ashare-signal-screening-real-") as directory:
        root = Path(directory)
        with (
            sqlite3.connect(f"file:{source / 'business.db'}?mode=ro", uri=True) as original,
            sqlite3.connect(root / "business.db") as copied,
        ):
            original.backup(copied)
            copied.execute("DELETE FROM sync_checkpoints")
            copied.execute("DELETE FROM job_logs")
            copied.execute("DELETE FROM jobs")
            copied.execute("DELETE FROM dataset_heads")
            copied.executemany(
                "INSERT INTO dataset_heads(dataset,version_id) VALUES (?,?)", snapshot.items()
            )
            copied.commit()
        (root / "market").symlink_to(source / "market", target_is_directory=True)
        settings = Settings(
            _env_file=None,
            data_dir=root,
            database_path=root / "business.db",
            api_access_token="",
            data_management_password="",
            tushare_token="",
            notification_auto_send=False,
            notification_encryption_key="",
        )
        app = create_app(settings)
        service = app.state.service
        client = TestClient(app)
        worker = Worker(settings)
        assert service.db.snapshot() == snapshot
        assert all(
            service.repository.version(version, resolve_coverage=False)["source"] != "fixture"
            for version in snapshot.values()
        )
        calendar = service.repository.read("trade_cal", snapshot, end_date=day).to_dicts()
        days = sorted(
            {r["cal_date"] for r in calendar if r.get("exchange") == "SSE" and r["is_open"] == 1}
        )
        assert days[-1] == day
        expected = {}
        for code in args.stocks:
            for config in CONFIGS[1:]:
                window = days[-warmup(StrategyConfig.model_validate(config)) - 1 :]
                bars = {
                    r["trade_date"]: r
                    for r in service.repository.read(
                        "daily", snapshot, [code], window[0], day
                    ).to_dicts()
                }
                factors = {
                    r["trade_date"]: r["adj_factor"]
                    for r in service.repository.read(
                        "adj_factor", snapshot, [code], window[0], day
                    ).to_dicts()
                }
                prices = [
                    np.array([float(bars[d][field]) * factors[d] / factors[day] for d in window])
                    for field in ("close", "high", "low")
                ]
                expected[code, config["id"]] = oracle(config, *prices)
        reports = []
        for direction in ("either", "buy", "sell"):
            payload = {
                "as_of_date": day,
                "mode": "signal",
                "universe": {"type": "custom", "codes": args.stocks},
                "signal_filter": {"direction": direction, "strategies": CONFIGS[1:]},
            }
            response = client.post("/api/v1/screening/run", json=payload)
            assert response.status_code == 202, response.text
            job = response.json()
            assert all(job["snapshot"][key] == value for key, value in snapshot.items())
            worker.run_once()
            finished = client.get(f"/api/v1/screening/runs/{job['id']}").json()
            assert finished["status"] == "succeeded", finished.get("error")
            assert finished["result"]["fixture_mode"] is False
            results = client.get(
                f"/api/v1/screening/runs/{job['id']}/results?match_status=all"
            ).json()
            assert len(results["items"]) == len(args.stocks)
            for row in results["items"]:
                for signal in row["signal_results"]:
                    assert signal["status"] == "checked", signal["reason"]
                    assert signal["direction"] == expected[row["stock_code"], signal["strategy_id"]]
                    assert signal["trade_date"] == day
                wanted = any(
                    expected[row["stock_code"], config["id"]] is not None
                    and (
                        direction == "either"
                        or expected[row["stock_code"], config["id"]] == direction
                    )
                    for config in CONFIGS[1:]
                )
                assert row["match_status"] == ("matched" if wanted else "not_matched")
            exported = list(
                csv.DictReader(
                    io.StringIO(
                        client.get(f"/api/v1/screening/runs/{job['id']}/export").text.lstrip(
                            "\ufeff"
                        )
                    )
                )
            )
            assert len(exported) == results["summary"]["matched"]
            assert all(
                len(json.loads(row["signal_results"])) == 5 and row["fixture_mode"] == "False"
                for row in exported
            )
            reports.append(
                {
                    "direction": direction,
                    "summary": finished["result"],
                    "signals": [
                        {
                            "stock_code": row["stock_code"],
                            "match_status": row["match_status"],
                            "signals": [
                                {
                                    "strategy": signal["strategy_id"],
                                    "direction": signal["direction"],
                                    "evidence": signal["evidence"],
                                }
                                for signal in row["signal_results"]
                            ],
                        }
                        for row in results["items"]
                    ],
                }
            )
        print(
            json.dumps(
                {
                    "trade_date": day,
                    "data_versions": snapshot,
                    "fixture_mode": False,
                    "independent_match": True,
                    "production_writes": 0,
                    "reports": reports,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
