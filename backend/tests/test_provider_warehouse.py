from pathlib import Path

import httpx
import polars as pl
import pytest
from ashare_lab.config import Settings
from ashare_lab.db import Database
from ashare_lab.providers import ProviderError, TushareProvider, filter_a_share_market, normalize
from ashare_lab.warehouse import MarketRepository


def test_parallel_provider_requests_share_rate_limit() -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from unittest.mock import patch

    clock = [100.0]
    starts: list[float] = []
    guard = threading.Lock()

    def sleep(delay: float) -> None:
        clock[0] += delay

    def respond(_: httpx.Request) -> httpx.Response:
        with guard:
            starts.append(clock[0])
        return httpx.Response(200, json={"code": 0, "data": {"fields": [], "items": []}})

    provider = TushareProvider(
        Settings(_env_file=None, tushare_token="test", requests_per_minute=40),
        httpx.Client(transport=httpx.MockTransport(respond)),
        sleep=sleep,
    )
    with patch("ashare_lab.providers.time.monotonic", side_effect=lambda: clock[0]):
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: provider.fetch("daily", {}), range(8)))
    assert clock[0] >= 110.5
    assert len(starts) == 8


def test_stock_basic_preserves_special_delisted_code(tmp_path: Path) -> None:
    from ashare_lab.factors import UniverseManager
    from ashare_lab.schemas import Universe

    frame = normalize(
        "stock_basic",
        pl.DataFrame(
            [
                {"ts_code": "T600018.SH", "exchange": "SSE", "list_date": "20000101"},
                {
                    "ts_code": "600001.SH",
                    "exchange": "SSE",
                    "list_date": "20000101",
                    "delist_date": "20200101",
                },
            ]
        ),
        "tushare",
        "special-code",
    )
    assert frame["stock_code"].to_list() == ["T600018.SH", "600001.SH"]
    db = Database(tmp_path / "business.db")
    db.initialize()
    repo = MarketRepository(db, tmp_path)
    repo.publish("stock_basic", frame, "D", "tushare", "special-code")
    assert [
        r["stock_code"]
        for r in UniverseManager(repo).resolve("2019-12-31", Universe(), db.snapshot())
    ] == ["600001.SH"]
    with pytest.raises(ProviderError):
        normalize("stock_basic", pl.DataFrame({"ts_code": ["INVALID.SH"]}), "tushare", "bad")


def test_provider_permissions_redact_token() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"code": -1, "msg": "权限不足 secret-abc"})
        )
    )
    provider = TushareProvider(
        Settings(_env_file=None, tushare_token="secret-abc"), client, sleep=lambda _: None
    )
    with pytest.raises(ProviderError) as error:
        provider.fetch("daily", {"trade_date": "20240101"})
    assert error.value.code == "permission_denied"
    assert "secret-abc" not in str(error.value)


def test_provider_retry_and_units() -> None:
    count = 0

    def respond(_: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        if count < 3:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "fields": [
                        "ts_code",
                        "trade_date",
                        "open",
                        "high",
                        "low",
                        "close",
                        "pre_close",
                        "pct_chg",
                        "vol",
                        "amount",
                    ],
                    "items": [["000001.SZ", "20240102", 10, 11, 9, 10.5, 10, 5, 100, 200]],
                },
            },
        )

    provider = TushareProvider(
        Settings(_env_file=None, tushare_token="dummy"),
        httpx.Client(transport=httpx.MockTransport(respond)),
        sleep=lambda _: None,
    )
    frame = normalize("daily", provider.fetch("daily", {}), "tushare", "batch")
    assert count == 3
    assert frame["volume"][0] == 10000
    assert frame["amount"][0] == 200000
    assert frame["daily_return"][0] == 0.05


def test_market_filter_preserves_malformed_codes_for_validation() -> None:
    raw = pl.DataFrame(
        {
            "ts_code": [
                "920090.BJ",
                "900901.SH",
                "200001.SZ",
                "000001.SZ",
                "688001.SH",
                "INVALID.SH",
                None,
            ]
        }
    )
    assert filter_a_share_market(raw)["ts_code"].to_list() == [
        "000001.SZ",
        "688001.SH",
        "INVALID.SH",
        None,
    ]


@pytest.mark.parametrize("code", ["T00018.SH", "T600018.SH", "T00018.SZ", "T000018.SZ"])
def test_market_filter_excludes_special_codes_without_rewriting(code: str) -> None:
    raw = pl.DataFrame(
        {
            "ts_code": [code, "000001.SZ", "600001.SH"],
            "trade_date": ["20060216"] * 3,
            "suspend_type": ["S"] * 3,
        }
    )
    frame = normalize("suspend_d", filter_a_share_market(raw), "tushare", "batch")
    assert frame["stock_code"].to_list() == ["000001.SZ", "600001.SH"]
    assert frame["trade_date"].to_list() == ["2006-02-16"] * 2
    assert raw["ts_code"].to_list() == [code, "000001.SZ", "600001.SH"]


@pytest.mark.parametrize("code", [None, "", "INVALID.SH", "T0018.SH", "T0000018.SH", "T00018.BJ"])
def test_market_filter_keeps_invalid_suspension_codes_for_validation(code: str | None) -> None:
    raw = pl.DataFrame({"ts_code": [code], "trade_date": ["20060216"]})
    with pytest.raises(ProviderError) as error:
        normalize("suspend_d", filter_a_share_market(raw), "tushare", "batch")
    assert error.value.code == "schema_error"
    assert "股票代码缺失或格式错误" in str(error.value)


@pytest.mark.parametrize("price", [None, 0.0, -1.0])
def test_invalid_a_share_price_identifies_stock_date_and_field(price: float | None) -> None:
    raw = pl.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "trade_date": "20111212",
                "open": 5.0,
                "high": 5.0,
                "low": 5.0,
                "close": 5.0,
                "pre_close": price,
            }
        ]
    )
    with pytest.raises(ProviderError) as error:
        normalize("daily", raw, "tushare", "batch")
    assert error.value.code == "schema_error"
    assert "000001.SZ" in str(error.value)
    assert "2011-12-12" in str(error.value)
    assert "pre_close" in str(error.value)


def test_immutable_versions_and_duplicate_sync(tmp_path: Path) -> None:
    db = Database(tmp_path / "business.db")
    db.initialize()
    repository = MarketRepository(db, tmp_path)
    data = pl.DataFrame(
        {"stock_code": ["000001.SZ"], "trade_date": ["2024-01-02"], "close": [10.0]}
    )
    first = repository.publish("daily", data, "day", "fixture", "one")
    repository.publish("daily", data, "day", "fixture", "two")
    assert repository.read("daily", db.snapshot()).height == 1
    repository.publish(
        "daily", data.with_columns(pl.lit(11.0).alias("close")), "day", "fixture", "three"
    )
    assert repository.read("daily", {"daily": first})["close"][0] == 10
    assert repository.read("daily", db.snapshot())["close"][0] == 11


def test_future_query_bound(tmp_path: Path) -> None:
    db = Database(tmp_path / "business.db")
    db.initialize()
    repository = MarketRepository(db, tmp_path)
    data = pl.DataFrame(
        {
            "stock_code": ["000001.SZ"] * 2,
            "trade_date": ["2024-01-02", "2024-01-03"],
            "close": [10.0, 999.0],
        }
    )
    repository.publish("daily", data, "range", "fixture", "one")
    assert repository.read("daily", db.snapshot(), end_date="2024-01-02")["close"].to_list() == [10]


def test_daily_basic_units_and_capability_permissions_are_explicit() -> None:
    frame = normalize(
        "daily_basic",
        pl.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": ["20240628"],
                "turnover_rate": [2.0],
                "close": [10.0],
                "dv_ttm": [4.0],
                "total_share": [100.0],
                "total_mv": [500.0],
            }
        ),
        "fixture",
        "batch",
    )
    assert frame["turnover_rate"].item() == 0.02
    assert frame["dv_ttm"].item() == 0.04
    assert frame["total_share"].item() == 1000000
    assert frame["total_mv"].item() == 5000000
    provider = TushareProvider(Settings(_env_file=None))
    assert provider.capabilities()["dividend"]["access"] == "unknown_until_requested"
