import csv
import io
from unittest.mock import patch

import polars as pl
import pytest
from ashare_lab.api import create_app
from ashare_lab.config import Settings
from ashare_lab.fixtures import load_fixture
from ashare_lab.worker import Worker
from fastapi.testclient import TestClient


@pytest.fixture
def system(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, database_path=tmp_path / "business.db")
    app = create_app(settings)
    load_fixture(app.state.service.repository)
    return TestClient(app), Worker(settings), app.state.service


def payload(client, **changes):
    return {
        "members": [{"stock_code": "000001.SZ"}, {"stock_code": "000002.SZ"}],
        "start_date": "2024-06-03",
        "end_date": "2024-06-28",
        "data_versions": client.get("/api/v1/backtest/options").json()["data_versions"],
        **changes,
    }


def test_actual_api_worker_results_csv_restore_and_old_snapshot(system):
    client, worker, service = system
    body = payload(client)
    response = client.post("/api/v1/portfolio/run", json=body)
    assert response.status_code == 202
    job_id = response.json()["id"]
    route = f"/api/v1/portfolio/runs/{job_id}"
    assert client.get(route + "/results").status_code == 409
    assert worker.run_once()
    result = client.get(route + "/results?page_size=1").json()
    assert result["summary"]["complete"]
    assert result["fixture_mode"]
    assert len(result["items"]) == 1 and result["total"] > 1
    assert client.get("/api/v1/jobs?kind=portfolio").status_code == 200
    positions = client.get(route + "/positions").json()
    assert len(positions["items"]) == 2
    detail = client.get(route + "/stocks/000001.SZ").json()
    assert detail["trades"] and detail["prices"]
    assert client.get(route + "/stocks/000099.SZ").status_code == 404
    assert client.get(route + "/positions?date=2024-01-01").status_code == 404
    for view in ["summary", "curves", "positions", "ledger"]:
        content = client.get(route + f"/export?view={view}").content.decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(content)))
        assert {r["account"] for r in rows} == {"portfolio", "benchmark"}
        assert rows[0]["fixture_mode"] == "True"
        assert rows[0]["engine_version"] == "1.0.0"
    frame = service.repository.read("daily", body["data_versions"]).with_columns(
        pl.lit(999).alias("open")
    )
    service.repository.publish("daily", frame, "new", "fixture", "new")
    assert client.get(route + "/results?page_size=1").json() == result
    service.db.update(job_id, status="running", result=None)
    worker.recover()
    assert worker.run_once()
    assert client.get(route + "/results?page_size=1").json() == result


def test_validation_cancel_versions_publication_and_type_isolation(system):
    client, worker, service = system
    for changes in [
        {"members": []},
        {"allocation": "custom"},
        {"members": [{"stock_code": "000099.SZ"}]},
        {"start_date": "2024-06-29"},
        {"data_versions": {"daily": "missing"}},
    ]:
        assert client.post(
            "/api/v1/portfolio/run", json=payload(client, **changes)
        ).status_code in {422, 409}
    job = client.post("/api/v1/portfolio/run", json=payload(client)).json()
    route = f"/api/v1/portfolio/runs/{job['id']}"
    assert client.delete(route).status_code == 409
    assert client.post(route + "/cancel").status_code == 200
    assert not worker.run_once()
    assert client.get(route + "/results").status_code == 409
    job = client.post("/api/v1/portfolio/run", json=payload(client)).json()
    old = service.db.job(job["id"])
    service.db.update(job["id"], snapshot={**old["snapshot"], "_portfolio_version": "future"})
    worker.run_once()
    assert service.db.job(job["id"])["status"] == "failed"
    job = client.post("/api/v1/portfolio/run", json=payload(client)).json()
    with patch("ashare_lab.worker.os.replace", side_effect=OSError("interrupted")):
        worker.run_once()
    assert service.db.job(job["id"])["result"] is None
    assert client.delete(f"/api/v1/backtest/runs/{job['id']}").status_code == 404
    assert client.delete("/api/v1/portfolio/runs").status_code == 200
    assert service.db.snapshot() == payload(client)["data_versions"]


def test_cancel_during_calculation_and_third_interruption(system):
    client, worker, service = system
    job = client.post("/api/v1/portfolio/run", json=payload(client)).json()
    original = worker.check
    calls = 0

    def check(job_id):
        nonlocal calls
        calls += 1
        if calls == 8:
            service.cancel(job_id, "portfolio")
        original(job_id)

    with patch.object(worker, "check", check):
        worker.run_once()
    assert service.db.job(job["id"])["status"] == "cancelled"
    assert service.db.job(job["id"])["result"] is None
    job = client.post("/api/v1/portfolio/run", json=payload(client)).json()
    for _ in range(3):
        service.db.update(job["id"], status="running")
        worker.recover()
    assert service.db.job(job["id"])["status"] == "failed"
