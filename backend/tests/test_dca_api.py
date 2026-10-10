from pathlib import Path
from unittest.mock import patch

import polars as pl
import pytest
from ashare_lab.api import create_app
from ashare_lab.config import Settings
from ashare_lab.fixtures import load_fixture
from ashare_lab.worker import Worker
from fastapi.testclient import TestClient


@pytest.fixture
def system(tmp_path: Path):
    settings = Settings(_env_file=None, data_dir=tmp_path, database_path=tmp_path / "business.db")
    app = create_app(settings)
    service = app.state.service
    load_fixture(service.repository)
    return TestClient(app), Worker(settings), service


def payload(client):
    return {
        "stock_code": "000001.SZ",
        "start_date": "2024-06-03",
        "end_date": "2024-06-28",
        "monthly_day": 3,
        "monthly_amount": "3000",
        "data_versions": client.get("/api/v1/dca/options").json()["data_versions"],
    }


def test_pinned_run_export_recovery_and_old_results(system):
    client, worker, service = system
    body = payload(client)
    response = client.post("/api/v1/dca/run", json=body)
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    assert client.get(f"/api/v1/dca/runs/{job_id}/results").status_code == 409
    service.db.update(job_id, status="running")
    worker.recover()
    assert worker.run_once()
    path = f"/api/v1/dca/runs/{job_id}/results"
    result = client.get(path).json()
    assert result["summary"]["complete"] is True, result
    assert result["summary"]["contributed"] == 3000
    assert result["summary"]["shares"] == 200
    assert result["summary"]["cash"] == 594.98
    assert result["summary"]["profit"] == pytest.approx(-405.02)
    export = client.get(f"/api/v1/dca/runs/{job_id}/export")
    assert (
        export.text.startswith("\ufeff") and "buy" in export.text and "survivors_v1" in export.text
    )
    assert client.get("/api/v1/jobs?kind=dca").json()[0]["id"] == job_id
    daily = service.repository.read("daily", body["data_versions"]).with_columns(
        pl.lit(999).alias("close")
    )
    service.repository.publish("daily", daily, "new", "fixture", "new")
    assert client.get(path).json() == result
    # Recovery after writing a file but before publishing DB result is a full recomputation.
    service.db.update(job_id, status="running", result=None)
    worker.recover()
    assert worker.run_once()
    assert client.get(path).json() == result


def test_invalid_inputs_versions_cancel_and_incompatible_engine(system):
    client, worker, service = system
    body = payload(client)
    for change in [
        {"monthly_day": 0},
        {"monthly_amount": "NaN"},
        {"start_date": "2024-06-29"},
        {"data_versions": {"daily": "missing"}},
        {"data_versions": {**body["data_versions"], "daily": body["data_versions"]["stock_basic"]}},
    ]:
        assert client.post("/api/v1/dca/run", json={**body, **change}).status_code in {409, 422}
    job = client.post("/api/v1/dca/run", json=body).json()
    assert client.post(f"/api/v1/dca/runs/{job['id']}/cancel").json()["status"] == "cancelled"
    assert not worker.run_once()
    job = client.post("/api/v1/dca/run", json=body).json()
    service.db.update(job["id"], snapshot={**body["data_versions"], "_dca_version": "future"})
    worker.run_once()
    assert service.job(job["id"])["status"] == "failed"
    assert client.get("/api/v1/dca/runs/missing/results").status_code == 404
    assert client.get(f"/api/v1/dca/runs/{job['id']}/results?page_size=201").status_code == 422


def test_cancel_during_accounting_no_result_is_published(system):
    client, worker, service = system
    job = client.post("/api/v1/dca/run", json=payload(client)).json()
    original = worker.check
    calls = 0

    def check(job_id):
        nonlocal calls
        calls += 1
        if calls == 4:
            service.cancel(job_id, "dca")
        original(job_id)

    with patch.object(worker, "check", check):
        worker.run_once()
    assert service.job(job["id"])["status"] == "cancelled"
    assert service.job(job["id"])["result"] is None
    assert not list(service.settings.data_dir.glob(f"results/{job['id']}/*.json"))


def test_missing_optional_coverage_returns_unknown_and_reads_bounded(system):
    client, worker, service = system
    body = payload(client)
    body["data_versions"].pop("dividend")
    job = client.post("/api/v1/dca/run", json=body).json()
    with patch.object(service.repository, "read", wraps=service.repository.read):
        # The worker owns a distinct repository, so inspect its actual reads.
        with patch.object(worker.repository, "read", wraps=worker.repository.read) as worker_read:
            worker.run_once()
            for call in worker_read.call_args_list:
                if call.args[0] in {"daily", "adj_factor", "stk_limit", "suspend_d"}:
                    assert call.args[4] == body["end_date"]
    result = client.get(f"/api/v1/dca/runs/{job['id']}/results").json()
    assert result["summary"]["profit"] is None
    assert result["summary"]["complete"] is False
    assert result["warnings"]


def test_calendar_gap_and_delisted_security_rejected(system):
    client, _, service = system
    body = payload(client)
    bad = {**body, "data_versions": {**body["data_versions"], "daily": "missing-id"}}
    assert client.post("/api/v1/dca/run", json=bad).status_code == 422
    calendar = service.repository.read("trade_cal", service.db.snapshot())
    # Publish a separately identified incomplete calendar without changing old versions.
    from ashare_lab.db import identifier, versions

    old = service.repository.version(body["data_versions"]["trade_cal"], resolve_coverage=False)
    gap = calendar.filter(pl.col("cal_date") != "2024-06-08")
    relative = f"market/trade_cal/gap-{identifier()}.parquet"
    gap.write_parquet(service.settings.data_dir / relative)
    vid = identifier()
    with service.db.engine.begin() as connection:
        connection.execute(
            versions.insert().values(**{**old, "id": vid, "manifest": {"gap": relative}})
        )
    assert (
        client.post(
            "/api/v1/dca/run",
            json={**body, "data_versions": {**body["data_versions"], "trade_cal": vid}},
        ).status_code
        == 409
    )
    basic = service.repository.read("stock_basic", service.db.snapshot()).with_columns(
        pl.lit("D").alias("list_status")
    )
    service.repository.publish("stock_basic", basic, "delisted", "fixture", "dca")
    bad = {**body, "data_versions": service.db.snapshot()}
    assert client.post("/api/v1/dca/run", json=bad).status_code == 422
    assert client.post("/api/v1/dca/run", json=body).status_code == 202


def test_results_remain_unpublished_when_atomic_replace_fails(system):
    client, worker, service = system
    body = payload(client)
    job = client.post("/api/v1/dca/run", json=body).json()
    with patch("ashare_lab.worker.os.replace", side_effect=OSError("synthetic interruption")):
        worker.run_once()
    assert service.job(job["id"])["status"] == "failed"
    assert service.job(job["id"])["result"] is None
    assert client.get(f"/api/v1/dca/runs/{job['id']}/results").status_code == 409
    service.db.update(job["id"], status="running")
    worker.recover()
    worker.run_once()
    result = client.get(f"/api/v1/dca/runs/{job['id']}/results").json()
    assert result["summary"]["contributed"] == 3000
    assert result["summary"]["shares"] == 200


def test_shipped_fixture_has_complete_calendar(system):
    client, _, _ = system
    from ashare_lab.fixtures import fixture_frames

    calendar = fixture_frames()["trade_cal"]
    assert calendar.filter(pl.col("cal_date") == "2024-06-08")["is_open"].item() == 0
    assert calendar.filter(pl.col("is_open") == 1).height == 100
    assert client.post("/api/v1/dca/run", json=payload(client)).status_code == 202


def test_options_calendar_and_legacy_metrics_are_version_bound(system):
    import json

    client, worker, service = system
    options = client.get("/api/v1/dca/options").json()
    assert options["calendar_start"] <= "2024-06-01"
    assert "2024-06-03" in options["trading_days"]
    assert "2024-06-08" not in options["trading_days"]
    assert max(options["trading_days"]) <= options["latest_market_date"]
    job = client.post("/api/v1/dca/run", json=payload(client)).json()
    worker.run_once()
    result = service.dca.result(job["id"])
    assert result["summary"]["annualized_return"] < 0
    assert result["summary"]["max_drawdown"] > 0
    # Simulate a previously persisted result: enrich only from its immutable curve.
    path = service.repository.root / service.db.job(job["id"])["result"]["path"]
    original = json.loads(path.read_text())
    del original["summary"]["annualized_return"]
    del original["summary"]["max_drawdown"]
    path.write_text(json.dumps(original))
    content = path.read_bytes()
    daily = service.repository.read("daily", options["data_versions"]).with_columns(
        pl.lit(999).alias("close")
    )
    service.repository.publish("daily", daily, "new", "fixture", "new")
    restored = service.dca.result(job["id"])
    assert restored["summary"] == result["summary"]
    assert path.read_bytes() == content
