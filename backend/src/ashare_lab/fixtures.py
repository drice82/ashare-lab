"""Deterministic, explicitly synthetic data; never used by the live provider."""

from datetime import date, timedelta
from typing import Any

import polars as pl

from ashare_lab.providers import normalize
from ashare_lab.warehouse import MarketRepository

FIXTURE_DATE = "2024-06-28"
EXPECTED_MATCHES = {
    "A": ["000001.SZ"],
    "B": ["000001.SZ", "000002.SZ"],
    "C": ["000003.SZ", "000005.SZ", "000006.SZ"],
    "D": ["000004.SZ"],
    "E": ["000005.SZ"],
}


def fixture_frames() -> dict[str, pl.DataFrame]:
    end = date.fromisoformat(FIXTURE_DATE)
    days: list[str] = []
    cursor = end
    while len(days) < 100:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    days.reverse()
    raw: dict[str, list[dict[str, Any]]] = {
        key: []
        for key in [
            "stock_basic",
            "namechange",
            "trade_cal",
            "daily",
            "adj_factor",
            "daily_basic",
            "dividend",
            "stk_limit",
            "suspend_d",
        ]
    }
    for i in range(1, 10):
        code = f"00000{i}.SZ"
        name = f"测试证券{i}" if i != 6 else "ST测试证券6"
        raw["stock_basic"].append(
            {
                "ts_code": code,
                "symbol": code[:6],
                "name": name,
                "exchange": "SZSE",
                "market": "主板",
                "list_status": "L",
                "list_date": days[-10] if i == 8 else "2000-01-01",
            }
        )
        raw["namechange"].append(
            {"ts_code": code, "name": name, "start_date": "2000-01-01", "ann_date": "1999-12-31"}
        )
        previous = 10.0
        for n, day in enumerate(days):
            if i == 8 and n < 90 or i == 7 and n == 99:
                continue
            if i == 1:
                close = 12.0 if n < 99 else 10.0
            elif i == 2:
                close = 15.0 if n < 60 else 12.0
            elif i in (3, 6):
                close = 10.0 * 1.004**n
            elif i == 4:
                close = 10.0 + 0.01 * n
            elif i == 5:
                close = 10.0 + 0.01 * n if n < 99 else 13.0
            else:
                close = 10.0
            volume = 1000.0
            if n == 99:
                if i == 4:
                    volume = 500
                if i == 5:
                    volume = 2000
            raw["daily"].append(
                {
                    "ts_code": code,
                    "trade_date": day,
                    "open": close,
                    "high": close * 1.005,
                    "low": close * 0.995,
                    "close": close,
                    "pre_close": previous,
                    "pct_chg": (close / previous - 1) * 100,
                    "vol": volume,
                    "amount": 50000,
                }
            )
            raw["adj_factor"].append({"ts_code": code, "trade_date": day, "adj_factor": 1.0})
            raw["daily_basic"].append(
                {"ts_code": code, "trade_date": day, "close": close, "pe": 10, "pb": 1}
            )
            raw["stk_limit"].append(
                {
                    "ts_code": code,
                    "trade_date": day,
                    "pre_close": previous,
                    "up_limit": previous * 1.1,
                    "down_limit": previous * 0.9,
                }
            )
            previous = close
        raw["dividend"].append(
            {
                "ts_code": code,
                "end_date": "2023-12-31",
                "ann_date": "2024-01-05",
                "imp_ann_date": "2024-01-08",
                "ex_date": "2024-01-10",
                "record_date": "2024-01-09",
                "pay_date": "2024-01-11",
                "div_proc": "实施",
                "stk_div": 0,
                "cash_div_tax": 1.0 if i != 9 else 0.0,
            }
        )
    raw["suspend_d"].append(
        {"ts_code": "000007.SZ", "trade_date": FIXTURE_DATE, "suspend_type": "S"}
    )
    cursor = date.fromisoformat(days[0])
    trading_dates = set(days)
    previous_day = None
    while cursor <= end:
        day = cursor.isoformat()
        is_open = day in trading_dates
        raw["trade_cal"].append(
            {
                "exchange": "SSE",
                "cal_date": day,
                "is_open": int(is_open),
                "pretrade_date": previous_day,
            }
        )
        if is_open:
            previous_day = day
        cursor += timedelta(days=1)
    return {
        key: normalize(key, pl.DataFrame(rows, infer_schema_length=None), "fixture", "fixed-v1")
        for key, rows in raw.items()
    }


def load_fixture(repository: MarketRepository) -> dict[str, str]:
    if any(repository.version(v)["source"] != "fixture" for v in repository.db.snapshot().values()):
        raise ValueError("不能向真实数据目录写入测试数据；请使用独立 DATA_DIR/DATABASE_PATH")
    frames = fixture_frames()
    days = frames["trade_cal"].filter(pl.col("is_open") == 1)["cal_date"].to_list()
    for dataset, frame in frames.items():
        repository.publish(
            dataset, frame, "fixture", "fixture", "fixed-v1", coverage_info={"trade_dates": days}
        )
        if dataset in {"dividend", "namechange"}:
            for code in frames["stock_basic"]["stock_code"].to_list():
                repository.publish(
                    dataset,
                    frame.clear(),
                    f"stock:{code}",
                    "fixture",
                    "fixed-v1",
                    coverage_info={"as_of_date": FIXTURE_DATE},
                )
    return repository.db.snapshot()
