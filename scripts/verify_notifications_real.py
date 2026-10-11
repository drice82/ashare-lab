"""Explicit date/versions, read-only production files, independent NumPy signal audit.

No API/Worker startup, provider calls, notifications or production writes. Migrate only
an isolated SQLite backup. This does not certify Bark/SMTP receipt or human sign-off.
"""

import argparse
import json
import sqlite3
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from ashare_lab.backtest import STRATEGY_VERSION
from ashare_lab.comparison import COMPARISON_VERSION
from ashare_lab.db import Database
from ashare_lab.schemas import StrategyConfig
from ashare_lab.signals import calculate_signal, warmup
from ashare_lab.warehouse import MarketRepository
from verify_comparison_real import CONFIGS, oracle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--trade-date", type=date.fromisoformat, required=True)
    parser.add_argument("--versions-file", type=Path, required=True)
    parser.add_argument("--stocks", nargs="+", default=["000001.SZ", "000338.SZ", "600519.SH"])
    args = parser.parse_args()
    source = args.data_dir.resolve()
    trade_date = args.trade_date.isoformat()
    snapshot = json.loads(args.versions_file.read_text())
    required = {"stock_basic", "trade_cal", "daily", "adj_factor", "suspend_d"}
    assert set(snapshot) == required, "明确指定五个通知依赖版本"
    reports = []
    with TemporaryDirectory(prefix="ashare-notification-real-") as directory:
        root = Path(directory)
        with (
            sqlite3.connect(f"file:{source / 'business.db'}?mode=ro", uri=True) as original,
            sqlite3.connect(root / "business.db") as copied,
        ):
            original.backup(copied)
        db = Database(root / "business.db")
        db.initialize()
        repository = MarketRepository(db, source)
        assert all(
            repository.version(value, resolve_coverage=False)["source"] != "fixture"
            for value in snapshot.values()
        )
        calendar = repository.read("trade_cal", snapshot, end_date=trade_date).to_dicts()
        days = sorted(
            {r["cal_date"] for r in calendar if r.get("exchange") == "SSE" and r["is_open"] == 1}
        )
        assert days[-1] == trade_date
        for stock in args.stocks:
            for raw_config in CONFIGS[1:]:
                config = StrategyConfig.model_validate(raw_config)
                window = days[-warmup(config) - 1 :]
                outcome = calculate_signal(
                    repository,
                    stock,
                    config,
                    trade_date,
                    snapshot,
                    STRATEGY_VERSION if config.id == "ma_cross" else COMPARISON_VERSION,
                )
                assert outcome["status"] == "checked", f"{stock}/{config.id}: {outcome['reason']}"
                bars = {
                    r["trade_date"]: r
                    for r in repository.read(
                        "daily", snapshot, [stock], window[0], trade_date
                    ).to_dicts()
                }
                factors = {
                    r["trade_date"]: float(r["adj_factor"])
                    for r in repository.read(
                        "adj_factor", snapshot, [stock], window[0], trade_date
                    ).to_dicts()
                }
                prices = [
                    np.array(
                        [
                            float(bars[day][field]) * factors[day] / factors[trade_date]
                            for day in window
                        ]
                    )
                    for field in ("close", "high", "low")
                ]
                expected = oracle(raw_config, *prices)
                assert outcome["direction"] == expected, f"{stock}/{config.id}: 独立算法不一致"
                reports.append(
                    {
                        "stock_code": stock,
                        "strategy": config.model_dump(),
                        "direction": outcome["direction"],
                        "evidence": outcome["evidence"],
                        "strategy_version": outcome["strategy_version"],
                        "independent_match": True,
                    }
                )
        db.engine.dispose()
    print(
        json.dumps(
            {
                "trade_date": trade_date,
                "data_versions": snapshot,
                "fixture_mode": False,
                "checks": reports,
                "count": len(reports),
                "production_writes": 0,
                "notification_sends": 0,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
