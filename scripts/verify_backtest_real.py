"""Real warehouse -> isolated API/Worker; independently audit prices, signals and cash.

Production SQLite is backed up read-only, immutable market files are reused read-only.
No provider calls, production jobs or credentials are modified or printed.
"""

import argparse
import calendar
import json
import sqlite3
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from ashare_lab.api import create_app
from ashare_lab.config import Settings
from ashare_lab.worker import Worker
from fastapi.testclient import TestClient


def cents(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--stock-code", default="000001.SZ")
    args = parser.parse_args()
    source = args.data_dir.resolve()
    reports = []
    with TemporaryDirectory(prefix="ashare-backtest-real-") as directory:
        root = Path(directory)
        with sqlite3.connect(f"file:{source / 'business.db'}?mode=ro", uri=True) as original:
            with sqlite3.connect(root / "business.db") as copied:
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
        )
        app = create_app(settings)
        service = app.state.service
        snapshot = service.db.snapshot()
        sources = {
            k: service.repository.version(v, resolve_coverage=False)["source"]
            for k, v in snapshot.items()
        }
        if "fixture" in sources.values():
            print("真实验收未执行：来源包含fixture")
            return 2
        latest = service.repository.latest_date("daily", snapshot)
        if not latest:
            print("真实验收未执行：没有日线")
            return 2
        earliest = (date.fromisoformat(latest) - timedelta(days=100)).isoformat()
        bars = service.repository.read(
            "daily", snapshot, [args.stock_code], earliest, latest
        ).to_dicts()
        days = sorted(r["trade_date"] for r in bars)
        if len(days) < 10:
            print("真实验收未完成：行情不足")
            return 2
        raw = {r["trade_date"]: r for r in bars}
        factors = {
            r["trade_date"]: Decimal(str(r["adj_factor"]))
            for r in service.repository.read(
                "adj_factor", snapshot, [args.stock_code], earliest, latest
            ).to_dicts()
        }
        events = service.repository.read("dividend", snapshot, [args.stock_code]).to_dicts()
        client = TestClient(app)
        for strategy in ("buy_hold", "ma_cross"):
            body = {
                "stock_code": args.stock_code,
                "start_date": days[6],
                "end_date": days[-1],
                "initial_cash": "100000",
                "strategy": strategy,
                "short_window": 2,
                "long_window": 5,
                "data_versions": snapshot,
            }
            response = client.post("/api/v1/backtest/run", json=body)
            assert response.status_code == 202, response.json()
            job_id = response.json()["id"]
            assert Worker(settings).run_once()
            job = service.db.job(job_id)
            assert job["status"] == "succeeded", job["error"]
            result = service.backtest.result(job_id)
            assert not result["fixture_mode"]
            assert result["summary"]["complete"], result["warnings"]
            cash, shares = Decimal(0), 0
            bought: str | None = None
            declared: list[tuple[str, Decimal]] = []
            for row in result["ledger"]:
                event, day = row["event"], row["date"]
                if event in {"contribution", "dividend"}:
                    cash += Decimal(str(row["amount"]))
                if event == "signal" and strategy == "ma_cross":
                    i = days.index(day)
                    differences = []
                    for offset in (1, 0):
                        window = days[i - 4 - offset : i + 1 - offset]
                        values = [
                            Decimal(str(raw[d]["close"])) * factors[d] / factors[window[-1]]
                            for d in window
                        ]
                        differences.append(sum(values[-2:]) / 2 - sum(values) / 5)
                    expected_buy = differences[0] <= 0 < differences[1]
                    expected_sell = differences[0] >= 0 > differences[1]
                    assert expected_buy or expected_sell
                    assert row["reason"] == ("金叉买入" if expected_buy else "死叉卖出")
                if event in {"buy", "sell"}:
                    price = Decimal(str(raw[day]["open"]))
                    assert Decimal(str(row["price"])) == price
                    assert row["signal_date"] < day
                    amount = cents(price * abs(row["shares_delta"]))
                    fee = cents(max(Decimal(5), amount * Decimal("0.0003"))) + cents(
                        amount * Decimal("0.00001")
                    )
                    if event == "sell":
                        fee += cents(amount * Decimal("0.0005"))
                        assert bought is not None and bought < day
                        acquire, sell = date.fromisoformat(bought), date.fromisoformat(day)
                        month_total = acquire.year * 12 + acquire.month
                        year, month = divmod(month_total, 12)
                        month_end = date(
                            year,
                            month + 1,
                            min(acquire.day, calendar.monthrange(year, month + 1)[1]),
                        )
                        year_end = date(
                            acquire.year + 1,
                            acquire.month,
                            min(
                                acquire.day, calendar.monthrange(acquire.year + 1, acquire.month)[1]
                            ),
                        )
                        rate = (
                            Decimal("0.2")
                            if sell <= month_end
                            else Decimal("0.1")
                            if sell <= year_end
                            else Decimal(0)
                        )
                        expected_tax = sum(
                            (cents(value * rate) for paid, value in declared if paid <= day),
                            Decimal(0),
                        )
                        assert Decimal(str(row["tax"])) == expected_tax
                        cash += amount - fee - expected_tax
                        declared = []
                    else:
                        cash -= amount + fee
                        bought = day
                    assert Decimal(str(row["fee"])) == fee
                    shares += row["shares_delta"]
                if event == "entitlement":
                    matching = [
                        e
                        for e in events
                        if e.get("record_date") == day and e.get("div_proc") == "实施"
                    ]
                    assert any(
                        cents(Decimal(str(e["cash_div_tax"])) * shares)
                        == Decimal(str(row["amount"]))
                        for e in matching
                    )
                    pay = next(
                        e["pay_date"]
                        for e in matching
                        if cents(Decimal(str(e["cash_div_tax"])) * shares)
                        == Decimal(str(row["amount"]))
                    )
                    declared.append((pay, Decimal(str(row["amount"]))))
                if event == "dividend_tax":
                    cash -= Decimal(str(row["tax"]))
                assert cents(cash) == Decimal(str(row["cash"])), row
                assert shares == row["shares"], row
            assert Decimal(str(result["summary"]["cash"])) == cash
            assert client.get(f"/api/v1/backtest/runs/{job_id}/export").status_code == 200
            if strategy == "ma_cross":
                assert result["summary"]["buy_count"] > 0 and result["summary"]["sell_count"] > 0
            reports.append(
                {
                    "strategy": strategy,
                    "scope": args.stock_code,
                    "start_date": body["start_date"],
                    "end_date": body["end_date"],
                    "short_window": 2,
                    "long_window": 5,
                    "summary": result["summary"],
                    "fixture_mode": False,
                    "isolated_snapshot": True,
                    "prices_signals_cash_fees_tax_verified": True,
                    "data_versions": snapshot,
                    "sources": sources,
                }
            )
    print(json.dumps(reports, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
