"""Real provider audit. All writes are confined to a temporary database and directory."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from ashare_lab.api import create_app
from ashare_lab.config import Settings
from ashare_lab.dividends import upcoming_dividends
from ashare_lab.providers import TushareProvider, normalize
from ashare_lab.worker import Worker
from fastapi.testclient import TestClient


def main() -> None:
    report = []
    with TemporaryDirectory(prefix="ashare-company-audit-") as directory:
        root = Path(directory)
        settings = Settings(
            _env_file=None,
            data_dir=root,
            database_path=root / "business.db",
            notification_auto_send=False,
        )
        if not settings.tushare_token.get_secret_value():
            print(json.dumps({"status": "blocked", "reason": "token_missing"}))
            return
        client = TestClient(create_app(settings))
        provider = TushareProvider(settings)
        worker = Worker(settings, provider)
        service = client.app.state.service
        # Read-only upstream requests; publish only into the isolated temporary repository.
        for code in ("600519.SH", "000001.SZ"):
            job = client.post(f"/api/v1/instruments/{code}/company/refresh").json()
            assert (
                client.post(f"/api/v1/instruments/{code}/company/refresh").json()["id"] == job["id"]
            )
            assert worker.run_once()
            payload = client.get(f"/api/v1/instruments/{code}/company").json()
            summary = {
                "stock_code": code,
                "fixture_mode": payload["fixture_mode"],
                "sections": {
                    key: {
                        "status": value["status"],
                        "rows": len(value["items"]),
                        "latest_report": value["items"][0].get("end_date")
                        if value["items"]
                        else None,
                    }
                    for key, value in payload["sections"].items()
                },
            }
            assert not payload["fixture_mode"]
            assert payload["sections"]["company"]["status"] == "ready"
            for key in ("indicators", "income", "balance", "cashflow"):
                section = payload["sections"][key]
                assert section["status"] == "ready", (code, key, section["status"])
                assert len(section["items"]) >= 8
            original = service.db.job(job["id"])["result"]
            # Resume a task with all successful sections checkpointed: no upstream calls.
            service.db.update(job["id"], status="running")
            worker.recover()
            assert worker.run_once()
            assert service.db.job(job["id"])["result"] == original
            assert service.db.snapshot() == {}
            raw = provider.fetch("dividend", {"ts_code": code})
            events = normalize("dividend", raw, "tushare", "audit").to_dicts()
            # Pick a real past implemented arrangement to verify publication and tax units.
            candidate = next(
                (
                    e
                    for e in reversed(events)
                    if e.get("imp_ann_date")
                    and e.get("ann_date")
                    and e.get("record_date")
                    and e.get("cash_div_tax", 0)
                    and max(e["ann_date"], e["imp_ann_date"]) < e["record_date"]
                ),
                None,
            )
            if candidate:
                day = max(candidate["ann_date"], candidate["imp_ann_date"])
                from datetime import timedelta

                as_of = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
                result = upcoming_dividends(as_of, 30, 8, [candidate], True)
                if result["events"]:
                    selected = result["events"][0]
                    assert (
                        Decimal(str(selected["cash_per_10"]))
                        == Decimal(str(candidate["cash_div_tax"])) * 10
                    )
                    summary["real_dividend_units_verified"] = True
            report.append(summary)
        print(
            json.dumps(
                {"status": "passed", "production_writes": 0, "reports": report}, ensure_ascii=False
            )
        )


if __name__ == "__main__":
    main()
