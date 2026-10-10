"""Verify a copied real catalog and read-only market files under a container memory limit.

The caller supplies an isolated data directory. Never point this at production: this
script checks that marker, and runs only isolated research jobs (no provider calls).
"""

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ashare_lab.api import create_app
from ashare_lab.config import Settings
from ashare_lab.factors import FactorEngine, UniverseManager
from ashare_lab.schemas import ScreeningRequest, Universe
from ashare_lab.screening import ScreeningEngine
from ashare_lab.worker import Worker
from fastapi.testclient import TestClient


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=900)
    args = parser.parse_args()
    root = args.data_dir
    if not (root / ".isolated-load-verification").is_file():
        raise SystemExit("requires isolated catalog marker")
    settings = Settings(_env_file=None, data_dir=root, database_path=root / "business.db")
    app = create_app(settings)
    service = app.state.service
    snapshot = service.db.snapshot()
    assert all(
        service.repository.version(v, resolve_coverage=False)["source"] != "fixture"
        for v in snapshot.values()
    )
    started = time.monotonic()
    for dataset, version in sorted(snapshot.items()):
        print(
            json.dumps(
                {"phase": "summary", "dataset": dataset, **service.repository.summary(version)}
            ),
            flush=True,
        )
    print(json.dumps({"phase": "prewarm", "seconds": time.monotonic() - started}), flush=True)
    day = service.repository.latest_date("daily", snapshot)
    assert day
    request = ScreeningRequest(as_of_date=day, rule_id="A")
    job = service.submit_screen(request)
    started = time.monotonic()
    assert Worker(settings).run_once()
    complete = service.db.job(job["id"])
    assert complete["status"] == "succeeded", complete["error"]
    elapsed = time.monotonic() - started
    assert elapsed < 300, elapsed
    print(
        json.dumps(
            {
                "phase": "full_screening",
                "seconds": elapsed,
                "day": day,
                "summary": complete["result"],
            }
        ),
        flush=True,
    )
    # Same real pinned snapshot: compare large/small batches and hand-check the
    # latest raw prices and last-trade dates independently of rule evaluation.
    instruments = UniverseManager(service.repository).resolve(day, Universe(), snapshot)[:12]
    reference = FactorEngine(service.repository).compute(day, instruments, snapshot)
    selected_request = ScreeningRequest(
        as_of_date=day,
        rule_id="A",
        universe=Universe(type="custom", codes=[r["stock_code"] for r in instruments]),
    )
    selected = ScreeningEngine(service.repository).run(selected_request, snapshot)
    assert [(r["stock_code"], r["factor_values"], r["data_quality_flags"]) for r in selected] == [
        (r["stock_code"], r["factor_values"], r["data_quality_flags"]) for r in reference
    ]
    for result in selected:
        raw = service.repository.read(
            "daily", snapshot, [result["stock_code"]], day, day
        ).to_dicts()
        assert result["factor_values"]["close"] == (raw[0]["close"] if raw else None)
    print(json.dumps({"phase": "real_factor_comparison", "stocks": len(selected)}), flush=True)
    client = TestClient(app)
    latencies = []

    def status(_):
        start = time.monotonic()
        response = client.get("/api/v1/market/status")
        assert response.status_code == 200
        value = response.json()
        assert value["fixture_mode"] is False and value["latest_market_date"] == day
        return time.monotonic() - start

    # Two independent pages poll together, including concurrent requests.
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=2) as pool:
        while time.monotonic() - started < args.seconds:
            latencies.extend(pool.map(status, range(2)))
            time.sleep(min(15, max(0, args.seconds - (time.monotonic() - started))))
    p95 = sorted(latencies)[int((len(latencies) - 1) * 0.95)]
    assert p95 <= 0.5, p95
    print(
        json.dumps(
            {
                "phase": "status_soak",
                "seconds": args.seconds,
                "requests": len(latencies),
                "p95_ms": p95 * 1000,
                "max_ms": max(latencies) * 1000,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
