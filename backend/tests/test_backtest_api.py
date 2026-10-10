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
        "stock_code": "000001.SZ",
        "start_date": "2024-06-03",
        "end_date": "2024-06-28",
        "initial_cash": "10000",
        "data_versions": client.get("/api/v1/backtest/options").json()["data_versions"],
        **changes,
    }


def test_pinned_versions_recovery_export_and_old_results(system):
    client, worker, service = system
    body = payload(client)
    job = client.post("/api/v1/backtest/run", json=body).json()
    job_id = job["id"]
    route = f"/api/v1/backtest/runs/{job_id}"
    assert client.get(route + "/results").status_code == 409
    service.db.update(job_id, status="running")
    worker.recover()
    assert worker.run_once()
    result = client.get(route + "/results").json()
    assert result["summary"]["complete"]
    assert result["summary"]["buy_count"] == 1
    buy = next(r for r in result["items"] if r["event"] == "buy")
    assert buy["date"] == "2024-06-04" and buy["signal_date"] == "2024-06-03"
    export = client.get(route + "/export")
    assert "signal_date" in export.text
    rows = list(csv.DictReader(io.StringIO(export.text.lstrip("\ufeff"))))
    assert all(row["fixture_mode"] == "True" for row in rows)
    assert all(row["engine_version"] == result["engine_version"] for row in rows)
    assert all(row["strategy_version"] == result["strategy_version"] for row in rows)
    assert client.get("/api/v1/jobs?kind=backtest").status_code == 200
    assert client.get("/api/v1/jobs?kind=sync").status_code == 403
    frame = service.repository.read("daily", body["data_versions"]).with_columns(
        pl.lit(999).alias("open")
    )
    service.repository.publish("daily", frame, "new", "fixture", "new")
    assert client.get(route + "/results").json() == result
    service.db.update(job_id, status="running", result=None)
    worker.recover()
    assert worker.run_once()
    assert client.get(route + "/results").json() == result


def test_invalid_input_cancel_and_incompatible_strategy(system):
    client, worker, service = system
    for changes in [
        {"short_window": 60},
        {"initial_cash": "NaN"},
        {"stamp_rate": -1},
        {"strategy": "code"},
        {"start_date": "2024-06-29"},
        {"data_versions": {"daily": "missing"}},
        {"stock_code": "000099.SZ"},
    ]:
        assert client.post("/api/v1/backtest/run", json=payload(client, **changes)).status_code in {
            409,
            422,
        }
    job = client.post("/api/v1/backtest/run", json=payload(client)).json()
    assert client.post(f"/api/v1/backtest/runs/{job['id']}/cancel").status_code == 200
    assert not worker.run_once()
    job = client.post("/api/v1/backtest/run", json=payload(client)).json()
    old = service.db.job(job["id"])
    service.db.update(job["id"], snapshot={**old["snapshot"], "_strategy_version": "future"})
    assert worker.run_once()
    assert client.get(f"/api/v1/backtest/runs/{job['id']}").json()["status"] == "failed"


def test_cancel_publication_failure_and_read_bounds(system):
    client, worker, service = system
    job_id = client.post("/api/v1/backtest/run", json=payload(client)).json()["id"]
    with patch("ashare_lab.worker.os.replace", side_effect=OSError):
        worker.run_once()
    assert service.db.job(job_id)["result"] is None

    assert service.db.job(job_id)["status"] == "failed"
    job_id = client.post("/api/v1/backtest/run", json=payload(client, strategy="ma_cross")).json()[
        "id"
    ]
    with patch.object(worker.repository, "read", wraps=worker.repository.read) as read:
        worker.run_once()
        for call in read.call_args_list:
            if call.args[0] in {"daily", "adj_factor", "stk_limit", "suspend_d"}:
                assert call.args[4] <= "2024-06-28"
    job_id = client.post("/api/v1/backtest/run", json=payload(client)).json()["id"]
    original = worker.check
    calls = 0

    def check(current):
        nonlocal calls
        calls += 1
        if calls == 4:
            service.cancel(current, "backtest")
        original(current)

    with patch.object(worker, "check", check):
        worker.run_once()
    assert service.db.job(job_id)["status"] == "cancelled"
    assert service.db.job(job_id)["result"] is None


def test_warmup_rows_without_published_coverage_are_unknown(system):
    client, worker, service = system
    body = payload(client, strategy="ma_cross")
    dates = (
        service.repository.read("trade_cal", body["data_versions"])
        .filter((pl.col("is_open") == 1) & (pl.col("cal_date") >= body["start_date"]))["cal_date"]
        .to_list()
    )
    frame = service.repository.read("daily", body["data_versions"])
    service.repository.publish(
        "daily",
        frame.clear(),
        "fixture",
        "fixture",
        "missing-warmup",
        coverage_info={"trade_dates": dates},
    )
    body["data_versions"] = service.db.snapshot()
    job_id = client.post("/api/v1/backtest/run", json=body).json()["id"]
    assert worker.run_once()
    result = service.backtest.result(job_id)
    assert result["summary"]["profit"] is None
    assert result["summary"]["confirmed_through"] is None
    assert any("预热" in warning for warning in result["warnings"])


def test_missing_warmup_calendar_date_is_unknown(system):
    client, worker, service = system
    job_id = client.post("/api/v1/backtest/run", json=payload(client, strategy="ma_cross")).json()[
        "id"
    ]
    original = worker.repository.read

    def read(dataset, *args, **kwargs):
        frame = original(dataset, *args, **kwargs)
        return frame.filter(pl.col("cal_date") != "2024-06-01") if dataset == "trade_cal" else frame

    with patch.object(worker.repository, "read", side_effect=read):
        assert worker.run_once()
    result = service.backtest.result(job_id)
    assert result["summary"]["profit"] is None
    assert any("预热日历" in warning for warning in result["warnings"])
