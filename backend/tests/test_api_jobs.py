from pathlib import Path

import polars as pl
import pytest
from ashare_lab.api import create_app
from ashare_lab.config import Settings
from ashare_lab.fixtures import FIXTURE_DATE, load_fixture
from ashare_lab.providers import ProviderError
from ashare_lab.schemas import ScreeningRequest
from ashare_lab.services import ApplicationService
from ashare_lab.worker import Worker
from fastapi.testclient import TestClient


@pytest.fixture
def system(tmp_path: Path) -> tuple[TestClient, Worker, ApplicationService]:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        database_path=tmp_path / "business.db",
        data_management_password="test-management",
    )
    app = create_app(settings)
    service = app.state.service
    load_fixture(service.repository)
    return (
        TestClient(app, headers={"X-Data-Management-Password": "test-management"}),
        Worker(settings),
        service,
    )


def run(client: TestClient, worker: Worker, rule: str = "A", parameters: dict | None = None) -> str:
    response = client.post(
        "/api/v1/screening/run",
        json={"as_of_date": FIXTURE_DATE, "rule_id": rule, "parameters": parameters or {}},
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    assert response.json()["status"] == "queued"
    assert worker.run_once()
    response = client.get(f"/api/v1/screening/runs/{job_id}")
    assert response.json()["status"] == "succeeded", response.text
    return job_id


def test_api_end_to_end_results_export_preset_bars(system: tuple) -> None:
    client, worker, _ = system
    job_id = run(client, worker)
    response = client.get(f"/api/v1/screening/runs/{job_id}/results")
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["stock_code"] == "000001.SZ"
    export = client.get(f"/api/v1/screening/runs/{job_id}/export")
    assert export.status_code == 200 and "000001.SZ" in export.text
    assert export.text.startswith("\ufeff")
    definition = {"as_of_date": FIXTURE_DATE, "rule_id": "A"}
    assert (
        client.post(
            "/api/v1/screening/presets", json={"name": "我的方案", "definition": definition}
        ).status_code
        == 201
    )
    assert client.get("/api/v1/screening/presets").json()[0]["name"] == "我的方案"
    bars = client.get(
        "/api/v1/market/bars", params={"stock_code": "000001.SZ", "run_id": job_id}
    ).json()
    assert bars["items"][-1]["trade_date"] == FIXTURE_DATE
    assert bars["items"][-1]["ma20"] == pytest.approx(11.9)
    status = client.get("/api/v1/market/status").json()
    assert status["fixture_mode"] is True
    assert status["system_config"]["timezone"] == "Asia/Shanghai"
    assert status["system_config"]["requests_per_minute"] == 40


def test_illegal_inputs_and_unknown_resources(system: tuple) -> None:
    client, worker, _ = system
    assert (
        client.post(
            "/api/v1/screening/run",
            json={"as_of_date": FIXTURE_DATE, "rule_id": "A", "parameters": {"dividend_min": -1}},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/screening/run", json={"as_of_date": "2024-06-29", "rule_id": "A"}
        ).status_code
        == 422
    )
    assert (
        client.get("/api/v1/market/bars", params={"stock_code": "';DROP TABLE jobs;"}).status_code
        == 422
    )
    assert client.get("/api/v1/data/sync/missing").status_code == 404
    assert (
        client.post(
            "/api/v1/data/sync",
            json={"mode": "initialize", "start_date": "2024-06-30", "end_date": "2024-01-01"},
        ).status_code
        == 422
    )
    job_id = run(client, worker)
    assert client.get(f"/api/v1/screening/runs/{job_id}/results?page_size=201").status_code == 422
    assert client.get(f"/api/v1/screening/runs/{job_id}/results?sort_by=__sql__").status_code == 422
    assert (
        client.get(
            "/api/v1/market/bars",
            params={"stock_code": "000001.SZ", "run_id": job_id, "end_date": "2024-07-01"},
        ).status_code
        == 422
    )


def test_server_sort_pagination_and_results_reproducibility(system: tuple) -> None:
    client, worker, service = system
    job_id = run(client, worker, "C")
    path = f"/api/v1/screening/runs/{job_id}/results"
    first = client.get(path, params={"page_size": 1, "sort_by": "close", "order": "desc"}).json()
    assert first["total"] == 3
    assert first["items"][0]["stock_code"] == "000003.SZ"
    filtered = client.get(path, params={"filter_factor": "close", "min_value": 14}).json()
    assert filtered["total"] == 2
    assert (
        client.get(
            path, params={"filter_factor": "close", "min_value": 20, "max_value": 10}
        ).status_code
        == 422
    )
    assert (
        client.get(path, params={"filter_factor": "close", "min_value": "nan"}).status_code == 422
    )
    old = client.get(path).json()
    data = service.repository.read("daily", service.db.snapshot()).with_columns(
        pl.lit(999.0).alias("close")
    )
    service.repository.publish("daily", data, "new", "fixture", "new")
    assert client.get(path).json() == old


def test_cancel_and_recover(system: tuple) -> None:
    client, worker, service = system
    job = client.post(
        "/api/v1/screening/run", json={"as_of_date": FIXTURE_DATE, "rule_id": "A"}
    ).json()
    assert client.post(f"/api/v1/screening/runs/{job['id']}/cancel").json()["status"] == "cancelled"
    assert not worker.run_once()
    job = service.submit_screen(ScreeningRequest(as_of_date=FIXTURE_DATE, rule_id="A"))
    service.db.update(job["id"], status="running")
    worker.recover()
    assert service.job(job["id"])["status"] == "queued"
    assert worker.run_once()
    assert service.job(job["id"])["status"] == "succeeded"


def test_failed_sync_does_not_damage_existing_data(system: tuple) -> None:
    client, worker, service = system
    before = service.db.snapshot()

    class Failing:
        source = "fixture"

        def fetch(self, dataset: str, params: dict[str, str]) -> pl.DataFrame:
            raise ProviderError("temporary_error", "模拟网络失败")

    worker.provider = Failing()
    job = client.post(
        "/api/v1/data/sync", json={"mode": "incremental", "end_date": FIXTURE_DATE}
    ).json()
    worker.run_once()
    assert service.job(job["id"])["status"] == "failed"
    assert service.db.snapshot() == before
    assert client.post(f"/api/v1/data/sync/{job['id']}/resume").status_code == 202


def test_access_control(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        database_path=tmp_path / "business.db",
        api_host="0.0.0.0",
        api_access_token="secret",
    )
    client = TestClient(create_app(settings))
    assert client.get("/api/v1/market/status").status_code == 401
    assert (
        client.get("/api/v1/market/status", headers={"Authorization": "Bearer secret"}).status_code
        == 200
    )
    assert client.get("/health").status_code == 200
