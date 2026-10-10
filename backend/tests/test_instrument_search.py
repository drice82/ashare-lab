from pathlib import Path

import polars as pl
import pytest
from ashare_lab.api import create_app
from ashare_lab.config import Settings
from ashare_lab.db import Database
from ashare_lab.providers import normalize
from ashare_lab.warehouse import MarketRepository
from fastapi.testclient import TestClient


@pytest.fixture
def search_system(tmp_path: Path):
    settings = Settings(_env_file=None, data_dir=tmp_path, database_path=tmp_path / "business.db")
    app = create_app(settings)
    service = app.state.service
    data = normalize(
        "stock_basic",
        pl.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "name": "平安银行",
                    "symbol": "000001",
                    "exchange": "SZSE",
                    "list_status": "L",
                    "list_date": "1991-04-03",
                    "cnspell": "PAYH",
                },
                {
                    "ts_code": "600000.SH",
                    "name": "浦发银行",
                    "symbol": "600000",
                    "exchange": "SSE",
                    "list_status": "L",
                    "list_date": "1999-11-10",
                },
                {
                    "ts_code": "600132.SH",
                    "name": "重庆啤酒",
                    "symbol": "600132",
                    "exchange": "SSE",
                    "list_status": "L",
                    "list_date": "1997-10-30",
                },
                {
                    "ts_code": "000002.SZ",
                    "name": "退市测试",
                    "symbol": "000002",
                    "exchange": "SZSE",
                    "list_status": "D",
                    "list_date": "1990-01-01",
                },
            ],
            infer_schema_length=None,
        ),
        "fixture",
        "search",
    )
    version = service.repository.publish("stock_basic", data, "search", "fixture", "search")
    return TestClient(app), service, version


@pytest.mark.parametrize(
    "term,code",
    [
        ("payh", "000001.SZ"),
        ("ping an yin hang", "000001.SZ"),
        ("平安", "000001.SZ"),
        ("０００００１", "000001.SZ"),
        ("pfyh", "600000.SH"),
        ("cqpj", "600132.SH"),
    ],
)
def test_search_aliases(search_system, term, code):
    client, _, _ = search_system
    response = client.get(
        "/api/v1/instruments", params={"search": term, "as_of_date": "2024-06-28"}
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["stock_code"] == code


def test_pinned_version_delisted_listing_and_invalid_version(search_system):
    client, service, version = search_system
    assert client.get("/api/v1/instruments", params={"search": "退市"}).json()["total"] == 0
    assert (
        client.get(
            "/api/v1/instruments", params={"search": "pfyh", "as_of_date": "1998-01-01"}
        ).json()["total"]
        == 0
    )
    data = service.repository.read("stock_basic", {"stock_basic": version}).with_columns(
        pl.lit("新版名称").alias("name"), pl.lit(None, dtype=pl.String).alias("cnspell")
    )
    service.repository.publish("stock_basic", data, "updated", "fixture", "search")
    assert (
        client.get(
            "/api/v1/instruments", params={"search": "平安", "data_version": version}
        ).json()["total"]
        == 1
    )
    assert client.get("/api/v1/instruments", params={"search": "平安"}).json()["total"] == 0
    assert client.get("/api/v1/instruments", params={"data_version": "missing"}).status_code == 422


def test_pinyin_column_evolution_repeated_publish_and_old_reads(tmp_path: Path):
    db = Database(tmp_path / "business.db")
    db.initialize()
    repo = MarketRepository(db, tmp_path)
    new = normalize(
        "stock_basic",
        pl.DataFrame([{"ts_code": "000001.SZ", "name": "平安银行", "cnspell": "PAYH"}]),
        "fixture",
        "search",
    )
    old = new.drop("cnspell")
    old_id = repo.publish("stock_basic", old, "old", "fixture", "search")
    new_id = repo.publish("stock_basic", new, "new", "fixture", "search")
    assert repo.read("stock_basic", {"stock_basic": new_id})["cnspell"].item() == "PAYH"
    assert "cnspell" not in repo.read("stock_basic", {"stock_basic": old_id}).columns
    again = repo.publish("stock_basic", new, "new", "fixture", "search")
    assert repo.version(new_id)["manifest"] == repo.version(again)["manifest"]
