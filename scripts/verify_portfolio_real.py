"""Read-only real snapshots: isolated API/Worker and independent ledger/signal audit."""

import argparse
import json
import resource
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import numpy as np
from ashare_lab.api import create_app
from ashare_lab.config import Settings
from ashare_lab.fixtures import fixture_frames
from ashare_lab.worker import Worker
from fastapi.testclient import TestClient
from verify_comparison_real import CONFIGS, PREHISTORY, oracle


def cents(value: Decimal) -> Decimal:
    return value.quantize(Decimal(".01"), rounding=ROUND_HALF_UP)


def rate(bought: str, sold: str) -> Decimal:
    import calendar

    start = date.fromisoformat(bought)

    def anniversary(months: int) -> date:
        year, month = divmod(start.year * 12 + start.month - 1 + months, 12)
        return date(year, month + 1, min(start.day, calendar.monthrange(year, month + 1)[1]))

    return (
        Decimal(".2")
        if date.fromisoformat(sold) <= anniversary(1)
        else Decimal(".1")
        if date.fromisoformat(sold) <= anniversary(12)
        else Decimal(0)
    )


def audit(
    output: dict[str, Any], raw: dict[str, dict[str, Any]], events: list[dict[str, Any]]
) -> None:
    """Independent FIFO rights reconstruction, cash, quantities, expenses and NAV."""
    parameters = output["parameters"]
    for account in [output, output["benchmark"]]:
        assert account["summary"]["complete"], account["warnings"]
        cash = Decimal(0)
        lots: dict[str, list[dict[str, Any]]] = {code: [] for code in raw}
        rights: dict[str, list[dict[str, Any]]] = {code: [] for code in raw}
        registered: set[tuple[str, str]] = set()
        expected_registration: list[Decimal] = []
        paid_tax = Decimal(0)
        for row in account["ledger"]:
            code, day, event = row["stock_code"], row["date"], row["event"]
            amount, tax = Decimal(str(row["amount"])), Decimal(str(row["tax"]))
            if event == "contribution":
                cash += amount
            elif event == "entitlement":
                if (code, day) not in registered:
                    registered.add((code, day))
                    public = [
                        e
                        for e in events
                        if e["stock_code"] == code
                        and e.get("record_date") == day
                        and e.get("div_proc") == "实施"
                        and e.get("ann_date", "9999") <= day
                        and e.get("imp_ann_date", "9999") <= day
                    ]
                    public = list(
                        {(e.get("end_date"), e.get("ex_date")): e for e in public}.values()
                    )
                    for dividend in public:
                        for lot in lots[code]:
                            if lot["shares"]:
                                gross = cents(
                                    Decimal(str(dividend["cash_div_tax"])) * lot["shares"]
                                )
                                right = dict(
                                    amount=gross,
                                    remaining=gross,
                                    shares=lot["shares"],
                                    due=Decimal(0),
                                    paid=False,
                                    pay=dividend["pay_date"],
                                )
                                lot["rights"].append(right)
                                rights[code].append(right)
                                expected_registration.append(gross)
                assert expected_registration and amount == expected_registration.pop(0), (
                    code,
                    day,
                    amount,
                    expected_registration,
                    public,
                )
            elif event == "dividend":
                right = next(e for e in rights[code] if not e["paid"] and e["pay"] <= day)
                assert amount == right["amount"] and tax == right["due"]
                right["paid"] = True
                cash += amount - tax
                paid_tax += tax
            elif event in {"buy", "sell"}:
                price = Decimal(str(raw[code][day]["open"]))
                quantity = abs(row["shares_delta"])
                assert row["signal_date"] < day
                assert price == Decimal(str(row["price"]))
                expected_amount = cents(price * quantity)
                fee = cents(
                    max(
                        Decimal(str(parameters["minimum_commission"])),
                        expected_amount * Decimal(str(parameters["commission_rate"])),
                    )
                ) + cents(expected_amount * Decimal(str(parameters["transfer_rate"])))
                expected_tax = Decimal(0)
                if event == "buy":
                    assert quantity >= (200 if code.startswith("688") else 100)
                    if not code.startswith("688"):
                        assert quantity % 100 == 0
                    lots[code].append(dict(date=day, shares=quantity, rights=[]))
                    assert amount == -expected_amount
                else:
                    fee += cents(expected_amount * Decimal(str(parameters["stamp_rate"])))
                    left = quantity
                    for lot in lots[code]:
                        sold = min(left, lot["shares"])
                        if sold:
                            assert lot["date"] < day
                            for right in lot["rights"]:
                                gross = (
                                    right["remaining"]
                                    if sold == right["shares"]
                                    else cents(right["remaining"] * sold / right["shares"])
                                )
                                portion_tax = cents(gross * rate(lot["date"], day))
                                right["shares"] -= sold
                                right["remaining"] -= gross
                                if right["paid"]:
                                    expected_tax += portion_tax
                                else:
                                    right["due"] += portion_tax
                            lot["shares"] -= sold
                            left -= sold
                        if not left:
                            break
                    assert left == 0 and amount == expected_amount
                    paid_tax += expected_tax
                assert tax == expected_tax
                assert Decimal(str(row["fee"])) == fee
                cash += amount - fee - tax
            assert cents(cash) == Decimal(str(row["cash"])), row
            assert cash >= 0
            if code:
                assert sum(lot["shares"] for lot in lots[code]) == row["shares"], row
        assert paid_tax == Decimal(str(account["summary"]["dividend_tax_paid"]))
        assert cash == Decimal(str(account["summary"]["cash"]))
        assert not expected_registration
        for point, holdings in zip(account["curve"], account["positions"], strict=True):
            assets = Decimal(str(holdings["cash"]))
            for position in holdings["items"]:
                if position["shares"]:
                    price = Decimal(
                        str(raw[position["stock_code"]][position["valuation_date"]]["close"])
                    )
                    value = cents(price * position["shares"])
                    assert value == Decimal(str(position["position_value"]))
                else:
                    value = Decimal(0)
                assets += (
                    value
                    + Decimal(str(position["receivable"]))
                    - Decimal(str(position["tax_liability"]))
                )
            assert assets == Decimal(str(point["total_assets"]))
        assert Decimal(str(account["summary"]["total_assets"])) == Decimal(
            str(account["curve"][-1]["total_assets"])
        )


def audit_signals(
    output: dict[str, Any], repository: Any, snapshot: dict[str, str], days: list[str]
) -> None:
    start, end = output["parameters"]["start_date"], output["parameters"]["end_date"]
    first = days.index(start)
    for member in output["parameters"]["members"]:
        code, config = member["stock_code"], member["strategy"]
        needed = PREHISTORY[[c["id"] for c in CONFIGS].index(config["id"])]
        raw = {
            r["trade_date"]: r
            for r in repository.read(
                "daily", snapshot, [code], days[first - needed], end
            ).to_dicts()
        }
        factors = {
            r["trade_date"]: r["adj_factor"]
            for r in repository.read(
                "adj_factor", snapshot, [code], days[first - needed], end
            ).to_dicts()
        }
        suspended = {
            r["trade_date"]
            for r in repository.read("suspend_d", snapshot, [code], start, end).to_dicts()
            if r.get("suspend_type") == "S"
        }
        expected = []
        for index in range(first, len(days)):
            day = days[index]
            if day > end:
                break
            if config["id"] == "buy_hold":
                if day == start:
                    expected.append((day, "buy"))
                continue
            if day in suspended:
                continue
            window = days[first - needed : index + 1]
            arrays = [
                np.array(
                    [
                        float(raw[value][field]) * float(factors[value]) / float(factors[day])
                        for value in window
                    ]
                )
                for field in ["close", "high", "low"]
            ]
            side = oracle(config, *arrays)
            if side:
                expected.append((day, side))
        actual = [
            (r["date"], "buy" if "买入" in r["reason"] else "sell")
            for r in output["ledger"]
            if r["event"] == "signal" and r["stock_code"] == code
        ]
        assert actual == expected, (code, config["id"])


def resource_scenario() -> dict[str, Any]:
    """Explicit synthetic five-year warehouse, two actual Worker research slots."""
    import polars as pl

    with TemporaryDirectory(prefix="portfolio-resource-") as directory:
        root = Path(directory)
        settings = Settings(
            _env_file=None,
            data_dir=root,
            database_path=root / "business.db",
            tushare_token="",
            api_access_token="",
            data_management_password="",
        )
        app = create_app(settings)
        repository = app.state.service.repository
        frames = fixture_frames()
        days = []
        cursor = date(2019, 1, 1)
        while cursor <= date(2025, 1, 3):
            if cursor.weekday() < 5:
                days.append(cursor.isoformat())
            cursor += timedelta(days=1)
        codes = [f"000{n:03}.SZ" for n in range(1, 21)]
        basic, bars, factors, limits, cal = [], [], [], [], []
        for code in codes:
            basic.append(
                {
                    **frames["stock_basic"].row(0, named=True),
                    "stock_code": code,
                    "name": f"资源测试{code}",
                    "list_date": "2000-01-01",
                }
            )
            for index, day in enumerate(days):
                price = 10 + (index % 40) / 10
                bars.append(
                    {
                        **frames["daily"].row(0, named=True),
                        "stock_code": code,
                        "trade_date": day,
                        "open": price,
                        "close": price,
                        "high": price + 0.1,
                        "low": price - 0.1,
                        "pre_close": price,
                    }
                )
                factors.append({"stock_code": code, "trade_date": day, "adj_factor": 1.0})
                limits.append(
                    {"stock_code": code, "trade_date": day, "up_limit": 100.0, "down_limit": 1.0}
                )
        cursor = date(2018, 12, 31)
        while cursor <= date(2025, 1, 6):
            cal.append(
                {
                    "exchange": "SSE",
                    "cal_date": cursor.isoformat(),
                    "is_open": int(cursor.weekday() < 5),
                }
            )
            cursor += timedelta(days=1)
        frames.update(
            stock_basic=pl.DataFrame(basic),
            trade_cal=pl.DataFrame(cal),
            daily=pl.DataFrame(bars),
            adj_factor=pl.DataFrame(factors),
            stk_limit=pl.DataFrame(limits),
            suspend_d=frames["suspend_d"].clear(),
            dividend=frames["dividend"].clear(),
        )
        for name, frame in frames.items():
            coverage: dict[str, Any] = {"trade_dates": days}
            if name == "dividend":
                coverage = {"kind": "history", "codes": codes, "as_of_date": days[-1]}
            repository.publish(
                name, frame, "portfolio-resource", "fixture", name, coverage_info=coverage
            )
        for code in codes:
            repository.publish(
                "dividend",
                frames["dividend"].clear(),
                f"stock:{code}",
                "fixture",
                "portfolio-resource",
                coverage_info={"as_of_date": days[-1]},
            )
        body = {
            "members": [{"stock_code": code, "strategy": {"id": "macd_cross"}} for code in codes],
            "start_date": "2020-01-02",
            "end_date": days[-1],
            "initial_cash": "900000",
            "data_versions": repository.db.snapshot(),
            "rebalance": "monthly",
        }
        client = TestClient(app)
        ids = [client.post("/api/v1/portfolio/run", json=body).json()["id"] for _ in range(2)]
        worker = Worker(settings)
        claimed = [worker.claim_job(sync=False) for _ in ids]
        assert all(claimed)
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(worker.execute_job, claimed))
        outputs = [app.state.service.portfolio.result(job_id) for job_id in ids]
        for output in outputs:
            assert output["summary"]["complete"], output["warnings"]
            assert len(output["curve"]) > 1000
        assert outputs[0]["summary"] == outputs[1]["summary"]
        return {
            "members": 20,
            "trading_days": len(outputs[0]["curve"]),
            "parallel_jobs": 2,
            "seconds": round(time.monotonic() - started, 2),
            "fixture_mode": True,
            "rss_peak_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--resource", action="store_true")
    args = parser.parse_args()
    source = args.data_dir.resolve()
    reports = []
    with TemporaryDirectory(prefix="portfolio-real-") as directory:
        root = Path(directory)
        with (
            sqlite3.connect(f"file:{source / 'business.db'}?mode=ro", uri=True) as original,
            sqlite3.connect(root / "business.db") as copied,
        ):
            original.backup(copied)
            for table in ["sync_checkpoints", "job_logs", "jobs"]:
                copied.execute(f"DELETE FROM {table}")
            copied.commit()
        (root / "market").symlink_to(source / "market", target_is_directory=True)
        settings = Settings(
            _env_file=None,
            data_dir=root,
            database_path=root / "business.db",
            tushare_token="",
            api_access_token="",
            data_management_password="",
        )
        app = create_app(settings)
        client, service = TestClient(app), app.state.service
        snapshot = service.db.snapshot()
        assert all(
            service.repository.version(v, resolve_coverage=False)["source"] != "fixture"
            for v in snapshot.values()
        )
        end = service.repository.latest_date("daily", snapshot)
        assert end
        days = sorted(
            {
                r["cal_date"]
                for r in service.repository.read("trade_cal", snapshot, end_date=end).to_dicts()
                if r.get("exchange") == "SSE" and r["is_open"] == 1
            }
        )
        target = (date.fromisoformat(end) - timedelta(days=365)).isoformat()
        start = next(day for day in days if day >= target)
        codes = ["000001.SZ", "000338.SZ", "600519.SH"]
        raw = {
            code: {
                r["trade_date"]: r
                for r in service.repository.read("daily", snapshot, [code], start, end).to_dicts()
            }
            for code in codes
        }
        events = service.repository.read("dividend", snapshot, codes).to_dicts()
        for configs, frequency in [
            ([CONFIGS[0]] * 3, "monthly"),
            (CONFIGS[1:4], "quarterly"),
            ([CONFIGS[4], CONFIGS[5], CONFIGS[0]], "weekly"),
        ]:
            body = {
                "members": [
                    {"stock_code": code, "strategy": config}
                    for code, config in zip(codes, configs, strict=True)
                ],
                "start_date": start,
                "end_date": end,
                "initial_cash": "900000",
                "rebalance": frequency,
                "data_versions": snapshot,
            }
            started = time.monotonic()
            response = client.post("/api/v1/portfolio/run", json=body)
            assert response.status_code == 202, response.text
            job_id = response.json()["id"]
            assert Worker(settings).run_once()
            assert service.db.job(job_id)["status"] == "succeeded", service.db.job(job_id)["error"]
            output = service.portfolio.result(job_id)
            assert not output["fixture_mode"]
            audit(output, raw, events)
            audit_signals(output, service.repository, snapshot, days)
            for view in ["summary", "curves", "positions", "ledger"]:
                assert (
                    client.get(f"/api/v1/portfolio/runs/{job_id}/export?view={view}").status_code
                    == 200
                )
            reports.append(
                {
                    "start_date": start,
                    "end_date": end,
                    "data_versions": snapshot,
                    "strategies": [c["id"] for c in configs],
                    "rebalance": frequency,
                    "summary": output["summary"],
                    "baseline_assets": output["benchmark"]["summary"]["total_assets"],
                    "seconds": round(time.monotonic() - started, 2),
                    "fixture_mode": False,
                }
            )
            print(json.dumps(reports[-1], ensure_ascii=False), flush=True)
    if args.resource:
        print(json.dumps({"resource": resource_scenario()}, ensure_ascii=False), flush=True)
    print(
        json.dumps(
            {
                "verified": True,
                "cases": len(reports),
                "rss_peak_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
