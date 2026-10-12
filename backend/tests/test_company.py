import polars as pl
import pytest
from ashare_lab.api import create_app
from ashare_lab.company import CompanyService, normalize_section
from ashare_lab.config import Settings
from ashare_lab.db import jobs
from ashare_lab.providers import ProviderError, normalize
from ashare_lab.worker import Worker
from fastapi.testclient import TestClient


class CompanyProvider:
    source = "fixture"

    def __init__(self):
        self.calls = []
        self.fail = set()

    def fetch(self, dataset, params):
        self.calls.append((dataset, params))
        if dataset in self.fail:
            raise ProviderError("permission_denied", "接口权限不足")
        if dataset == "stock_company":
            return pl.DataFrame(
                [
                    {
                        "ts_code": "000001.SZ",
                        "com_name": "测试公司",
                        "reg_capital": 100,
                        "setup_date": "19990101",
                        "introduction": "静态企业介绍",
                    }
                ]
            )
        if dataset == "fina_mainbz":
            return pl.DataFrame(
                [
                    {
                        "ts_code": "000001.SZ",
                        "end_date": "20260630",
                        "bz_item": "主营",
                        "bz_sales": 800,
                        "bz_cost": 500,
                        "bz_profit": 300,
                        "curr_type": "CNY",
                        "update_flag": "1",
                    }
                ]
            )
        return pl.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "end_date": "20260630",
                    "ann_date": "20260801",
                    "report_type": "1",
                    "update_flag": "1",
                    "roe": 12.5,
                    "revenue": 800,
                    "n_cashflow_act": 250,
                }
            ]
        )


def test_company_valuation_keeps_normalized_yuan_and_company_original_units(repository):
    # Supplier 1,234,567.89 ten-thousand yuan becomes 12,345,678,900 yuan once.
    frame = normalize(
        "daily_basic",
        pl.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": ["20240628"],
                "close": [10.0],
                "total_mv": [1234567.89],
                "circ_mv": [678901.23],
            }
        ),
        "fixture",
        "unit-check",
    )
    repository.publish("daily_basic", frame, "2024-06-28", "fixture", "unit-check")
    settings = Settings(
        _env_file=None, data_dir=repository.root, database_path=repository.db.engine.url.database
    )
    service = CompanyService(settings, repository.db, repository)
    service.refresh("000001.SZ")
    assert Worker(settings, CompanyProvider()).run_once()
    result = service.read("000001.SZ")
    assert result["valuation"]["total_mv"] == pytest.approx(12345678900, rel=0, abs=0.00001)
    assert result["valuation"]["circ_mv"] == pytest.approx(6789012300, rel=0, abs=0.00001)
    assert result["sections"]["company"]["items"][0]["reg_capital"] == 100
    assert result["sections"]["products"]["items"][0]["bz_sales"] == 800


def test_financial_revision_dates_and_units():
    rows = [
        {
            "ts_code": "000001.SZ",
            "end_date": "20260630",
            "ann_date": "20260701",
            "report_type": "1",
            "revenue": 100,
        },
        {
            "ts_code": "000001.SZ",
            "end_date": "20260630",
            "ann_date": "20260801",
            "report_type": "1",
            "revenue": 120,
            "update_flag": "1",
        },
        {
            "ts_code": "000001.SZ",
            "end_date": "20260630",
            "ann_date": "20260801",
            "report_type": "6",
            "revenue": 999,
        },
    ]
    result = normalize_section("income", pl.DataFrame(rows), "000001.SZ", "2026-10-11")
    assert len(result) == 1
    assert result[0]["revenue"] == 120
    assert result[0]["end_date"] == "2026-06-30"
    assert result[0]["ann_date"] == "2026-08-01"
    assert (
        normalize_section(
            "fina_indicator",
            pl.DataFrame(
                [
                    {
                        "ts_code": "000001.SZ",
                        "end_date": "20260630",
                        "ann_date": "20260801",
                        "roe": 12.5,
                    }
                ]
            ),
            "000001.SZ",
            "2026-10-11",
        )[0]["roe"]
        == 12.5
    )


def test_company_task_dedup_cache_partial_and_resume(repository):
    settings = Settings(
        _env_file=None, data_dir=repository.root, database_path=repository.db.engine.url.database
    )
    service = CompanyService(settings, repository.db, repository)
    job = service.refresh("000001.SZ")
    assert service.refresh("000001.SZ")["id"] == job["id"]
    provider = CompanyProvider()
    provider.fail.add("cashflow")
    worker = Worker(settings, provider)
    heads = repository.db.snapshot()
    assert worker.run_once()
    first = service.read("000001.SZ")
    assert first["sections"]["company"]["items"][0]["com_name"] == "测试公司"
    assert first["sections"]["cashflow"]["status"] == "permission_denied"
    assert first["fixture_mode"] is True
    assert first["stale"] is False
    assert repository.db.snapshot() == heads
    assert service.refresh("000001.SZ")["id"] == job["id"]  # failure cooldown
    with repository.db.engine.begin() as conn:
        conn.execute(
            jobs.update()
            .where(jobs.c.id == job["id"])
            .values(updated_at="2026-01-01T00:00:00+00:00")
        )
    retry = service.refresh("000001.SZ")
    assert retry["id"] != job["id"]
    provider.fail.clear()
    provider.calls.clear()
    assert worker.run_once()
    assert len(provider.calls) == 12
    assert {name for name, _ in provider.calls} == {"cashflow"}
    assert service.read("000001.SZ")["sections"]["cashflow"]["status"] == "ready"
    # A crash after persisting one section resumes without requesting it again.
    repository.db.update(retry["id"], status="running")
    provider.calls.clear()
    worker.recover()
    worker.run_once()
    assert provider.calls == []
    assert repository.db.snapshot() == heads


def test_company_endpoints_isolation_and_bad_code(repository):
    settings = Settings(
        _env_file=None, data_dir=repository.root, database_path=repository.db.engine.url.database
    )
    client = TestClient(create_app(settings))
    assert client.get("/api/v1/instruments/INVALID/company").status_code == 422
    response = client.get("/api/v1/instruments/000001.SZ/company")
    assert response.status_code == 200
    assert response.json()["sections"] == {}
    job = client.post("/api/v1/instruments/000001.SZ/company/refresh").json()
    assert client.get(f"/api/v1/instruments/000002.SZ/company/jobs/{job['id']}").status_code == 404
    assert Worker(settings, CompanyProvider()).run_once()
    assert (
        client.get(f"/api/v1/instruments/000001.SZ/company/jobs/{job['id']}").json()["status"]
        == "succeeded"
    )


def test_archived_financial_report_and_conflict_are_not_current():
    import pytest

    base = {
        "ts_code": "000001.SZ",
        "end_date": "20260630",
        "ann_date": "20260801",
        "report_type": "1",
        "revenue": 120,
        "update_flag": "1",
    }
    archived = {**base, "report_type": "5", "revenue": 999}
    assert (
        normalize_section("income", pl.DataFrame([base, archived]), "000001.SZ", "2026-10-11")[0][
            "revenue"
        ]
        == 120
    )
    with pytest.raises(ProviderError):
        normalize_section(
            "income", pl.DataFrame([base, {**base, "revenue": 130}]), "000001.SZ", "2026-10-11"
        )
    with pytest.raises(ProviderError):
        normalize_section(
            "stock_company", pl.DataFrame([{"ts_code": "000002.SZ"}]), "000001.SZ", "2026-10-11"
        )
    assert (
        normalize_section(
            "income", pl.DataFrame([{**base, "ann_date": "20261101"}]), "000001.SZ", "2026-10-11"
        )
        == []
    )


def test_actual_interruption_and_immutable_result(repository):
    import pytest

    settings = Settings(
        _env_file=None, data_dir=repository.root, database_path=repository.db.engine.url.database
    )
    service = CompanyService(settings, repository.db, repository)
    job = service.refresh("000001.SZ")
    worker = Worker(settings, CompanyProvider())
    original = worker.check

    def crash(job_id):
        if "company" in (repository.db.job(job_id).get("result") or {}).get("sections", {}):
            raise SystemExit("simulated process exit after checkpoint")
        original(job_id)

    worker.check = crash
    with pytest.raises(SystemExit):
        worker.run_once()
    assert repository.db.job(job["id"])["status"] == "running"
    worker.check = original
    worker.provider.calls.clear()
    worker.recover()
    assert worker.run_once()
    assert "stock_company" not in {dataset for dataset, _ in worker.provider.calls}
    result = repository.db.job(job["id"])["result"]
    target = repository.root / result["path"]
    original_bytes = target.read_bytes()
    repository.db.update(job["id"], status="running")
    worker.recover()
    worker.run_once()
    assert target.read_bytes() == original_bytes
    assert repository.db.job(job["id"])["result"] == result


def test_forced_refresh_and_truncated_response(repository):
    settings = Settings(
        _env_file=None, data_dir=repository.root, database_path=repository.db.engine.url.database
    )
    service = CompanyService(settings, repository.db, repository)
    worker = Worker(settings, CompanyProvider())
    first = service.refresh("000001.SZ")
    worker.run_once()
    assert service.refresh("000001.SZ")["id"] == first["id"]
    second = service.refresh("000001.SZ", force=True)
    assert second["id"] != first["id"]
    assert (
        service.read("000001.SZ")["fixture_mode"] is True
    )  # cached fixture remains labelled while queued
    worker.provider.calls.clear()
    worker.run_once()
    assert "stock_company" in {dataset for dataset, _ in worker.provider.calls}

    class Truncated(CompanyProvider):
        def fetch(self, dataset, params):
            frame = super().fetch(dataset, params)
            return pl.concat([frame] * 100) if dataset == "income" else frame

    worker = Worker(settings, Truncated())
    service.refresh("000001.SZ", force=True)
    worker.run_once()
    assert service.read("000001.SZ")["sections"]["income"]["status"] == "truncated"
