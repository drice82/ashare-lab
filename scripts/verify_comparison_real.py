"""Read-only real snapshot -> isolated API/Worker, independent NumPy signal audit."""

import argparse
import json
import sqlite3
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import numpy as np
from ashare_lab.api import create_app
from ashare_lab.config import Settings
from ashare_lab.worker import Worker
from fastapi.testclient import TestClient

CONFIGS = [
    {"id": "buy_hold"},
    {"id": "ma_cross", "short_window": 20, "long_window": 60},
    {"id": "macd_cross", "fast_window": 12, "slow_window": 26, "signal_window": 9},
    {"id": "rsi_reversal", "period": 14, "lower": 30, "upper": 70},
    {"id": "donchian_breakout", "entry_window": 20, "exit_window": 10},
    {"id": "bollinger_reversion", "period": 20, "deviations": 2},
]
PREHISTORY = [0, 60, 139, 71, 20, 20]


def smoothing(values: np.ndarray, size: int, alpha: float) -> np.ndarray:
    result = [np.mean(values[:size])]
    for value in values[size:]:
        result.append(alpha * value + (1 - alpha) * result[-1])
    return np.array(result)


def direction(value: float, scale: float = 1) -> int:
    return 0 if abs(value) <= max(1, scale) * 1e-12 else 1 if value > 0 else -1


def cross(values: np.ndarray) -> str | None:
    previous, current = (direction(float(v), float(np.max(np.abs(values)))) for v in values[-2:])
    return "buy" if previous <= 0 < current else "sell" if previous >= 0 > current else None


def oracle(
    config: dict[str, Any], close: np.ndarray, high: np.ndarray, low: np.ndarray
) -> str | None:
    name = config["id"]
    if name == "ma_cross":
        differences = [
            np.mean(close[end - config["short_window"] : end])
            - np.mean(close[end - config["long_window"] : end])
            for end in [len(close) - 1, len(close)]
        ]
        return cross(np.array(differences))
    if name == "macd_cross":
        fast = smoothing(close, config["fast_window"], 2 / (config["fast_window"] + 1))
        slow = smoothing(close, config["slow_window"], 2 / (config["slow_window"] + 1))
        dif = fast[-len(slow) :] - slow
        dea = smoothing(dif, config["signal_window"], 2 / (config["signal_window"] + 1))
        return cross(dif[-2:] - dea[-2:])
    if name == "rsi_reversal":
        delta = np.diff(close)
        gains = smoothing(np.maximum(delta, 0), config["period"], 1 / config["period"])
        losses = smoothing(np.maximum(-delta, 0), config["period"], 1 / config["period"])
        values = np.array(
            [
                50 if gain == loss == 0 else 100 if loss == 0 else 100 - 100 / (1 + gain / loss)
                for gain, loss in zip(gains[-2:], losses[-2:], strict=True)
            ]
        )
        if cross(values - config["upper"]) == "sell":
            return "sell"
        return "buy" if cross(values - config["lower"]) == "buy" else None
    if name == "donchian_breakout":
        return (
            "buy"
            if close[-1] > np.max(high[-config["entry_window"] - 1 : -1])
            else "sell"
            if close[-1] < np.min(low[-config["exit_window"] - 1 : -1])
            else None
        )
    if name == "bollinger_reversion":
        windows = [close[end - config["period"] : end] for end in [len(close) - 1, len(close)]]
        middle = np.array([np.mean(window) for window in windows])
        lower = middle - config["deviations"] * np.array(
            [np.std(window, ddof=0) for window in windows]
        )
        if cross(close[-2:] - middle) == "buy":
            return "sell"
        return "buy" if cross(close[-2:] - lower) == "buy" else None
    return None


def cents(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--stocks", nargs="+", default=["000001.SZ", "000338.SZ", "600519.SH"])
    parser.add_argument("--initial-cash", default="100000")
    args = parser.parse_args()
    source = args.data_dir.resolve()
    with TemporaryDirectory(prefix="ashare-comparison-real-") as directory:
        root = Path(directory)
        with (
            sqlite3.connect(f"file:{source / 'business.db'}?mode=ro", uri=True) as original,
            sqlite3.connect(root / "business.db") as copied,
        ):
            original.backup(copied)
            copied.execute("DELETE FROM sync_checkpoints")
            copied.execute("DELETE FROM job_logs")
            copied.execute("DELETE FROM jobs")
            copied.commit()
        (root / "market").symlink_to(source / "market", target_is_directory=True)
        settings = Settings(
            _env_file=None,
            data_dir=root,
            database_path=root / "business.db",
            api_access_token="",
            data_management_password="",
            tushare_token="",
        )
        app = create_app(settings)
        service = app.state.service
        client = TestClient(app)
        snapshot = service.db.snapshot()
        assert all(
            service.repository.version(value, resolve_coverage=False)["source"] != "fixture"
            for value in snapshot.values()
        )
        end = service.repository.latest_date("daily", snapshot)
        assert end
        calendar = service.repository.read("trade_cal", snapshot, end_date=end).to_dicts()
        days = sorted(
            {
                row["cal_date"]
                for row in calendar
                if row.get("exchange") == "SSE" and row["is_open"] == 1
            }
        )
        target = (date.fromisoformat(end) - timedelta(days=365)).isoformat()
        start = next(day for day in days if day >= target)
        first = days.index(start)
        assert first >= max(PREHISTORY)
        for code in args.stocks:
            body = {
                "stock_code": code,
                "start_date": start,
                "end_date": end,
                "initial_cash": args.initial_cash,
                "data_versions": snapshot,
                "strategies": CONFIGS,
            }
            response = client.post("/api/v1/backtest/compare", json=body)
            assert response.status_code == 202, response.text
            job_id = response.json()["id"]
            assert Worker(settings).run_once()
            job = service.db.job(job_id)
            assert job["status"] == "succeeded", job["error"]
            output = service.backtest.result(job_id)
            assert not output["fixture_mode"]
            raw = {
                row["trade_date"]: row
                for row in service.repository.read(
                    "daily", snapshot, [code], days[first - max(PREHISTORY)], end
                ).to_dicts()
            }
            factors = {
                row["trade_date"]: float(row["adj_factor"])
                for row in service.repository.read(
                    "adj_factor", snapshot, [code], days[first - max(PREHISTORY)], end
                ).to_dicts()
            }
            suspended = {
                row["trade_date"]
                for row in service.repository.read(
                    "suspend_d", snapshot, [code], start, end
                ).to_dicts()
                if row.get("suspend_type") == "S"
            }
            for config, needed, result in zip(
                CONFIGS, PREHISTORY, output["strategies"], strict=True
            ):
                assert result["summary"]["complete"], result["warnings"]
                signals = [
                    (row["date"], "buy" if "买入" in row["reason"] else "sell")
                    for row in result["ledger"]
                    if row["event"] == "signal"
                ]
                expected = []
                for day in days[first:]:
                    if config["id"] == "buy_hold":
                        if day == start:
                            expected.append((day, "buy"))
                        continue
                    if day in suspended:
                        continue
                    stop = days.index(day) + 1
                    window = days[first - needed : stop]
                    arrays = [
                        np.array(
                            [
                                float(raw[value][field]) * factors[value] / factors[day]
                                for value in window
                            ]
                        )
                        for field in ["close", "high", "low"]
                    ]
                    side = oracle(config, *arrays)
                    if side:
                        expected.append((day, side))
                assert signals == expected, (code, config["id"], signals, expected)
                cash, shares = Decimal(0), 0
                for row in result["ledger"]:
                    event = row["event"]
                    if event in {"contribution", "dividend"}:
                        cash += Decimal(str(row["amount"]))
                    if event in {"buy", "sell"}:
                        assert row["signal_date"] < row["date"]
                        price = Decimal(str(raw[row["date"]]["open"]))
                        assert Decimal(str(row["price"])) == price
                        amount = cents(price * abs(row["shares_delta"]))
                        fee = cents(max(Decimal(5), amount * Decimal("0.0003"))) + cents(
                            amount * Decimal("0.00001")
                        )
                        if event == "sell":
                            fee += cents(amount * Decimal("0.0005"))
                        assert Decimal(str(row["fee"])) == fee
                        assert abs(Decimal(str(row["amount"]))) == amount
                        cash += Decimal(str(row["amount"])) - fee - Decimal(str(row["tax"]))
                        shares += row["shares_delta"]
                    if event == "dividend_tax":
                        cash -= Decimal(str(row["tax"]))
                    assert cash == Decimal(str(row["cash"])) and shares == row["shares"], row
                assert cash == Decimal(str(result["summary"]["cash"]))
            # Same recursive seed even when other strategies are not selected.
            response = client.post(
                "/api/v1/backtest/compare", json={**body, "strategies": [CONFIGS[2]]}
            )
            assert response.status_code == 202
            assert Worker(settings).run_once()
            alone = service.backtest.result(response.json()["id"])["strategies"][0]
            for field in ["summary", "curve", "ledger"]:
                assert alone[field] == output["strategies"][2][field]
            for view in ["summary", "curves", "ledger"]:
                assert (
                    client.get(f"/api/v1/backtest/runs/{job_id}/export?view={view}").status_code
                    == 200
                )
            print(
                json.dumps(
                    {
                        "stock": code,
                        "initial_cash": args.initial_cash,
                        "start": start,
                        "end": end,
                        "fixture_mode": False,
                        "independent_signals_prices_cash_fees": True,
                        "strategies": [
                            {
                                "id": result["strategy_id"],
                                "assets": result["summary"]["total_assets"],
                                "buys": result["summary"]["buy_count"],
                                "sells": result["summary"]["sell_count"],
                            }
                            for result in output["strategies"]
                        ],
                        "data_versions": snapshot,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )


if __name__ == "__main__":
    main()
