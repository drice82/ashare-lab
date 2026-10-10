import math
import re
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
import polars as pl

from ashare_lab.config import Settings

LIMITS = {
    "stock_basic": 6000,
    "namechange": 5000,
    "trade_cal": 10000,
    "daily": 6000,
    "adj_factor": 6000,
    "daily_basic": 6000,
    "dividend": 2000,
    "stk_limit": 5800,
    "suspend_d": 5000,
}
FIELDS = {
    "stock_basic": "ts_code,symbol,name,cnspell,area,industry,market,exchange,list_status,list_date,delist_date",
    "namechange": "ts_code,name,start_date,end_date,ann_date,change_reason",
    "trade_cal": "exchange,cal_date,is_open,pretrade_date",
    "daily": "ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol,amount",
    "adj_factor": "ts_code,trade_date,adj_factor",
    "daily_basic": "ts_code,trade_date,close,turnover_rate,volume_ratio,pe,pe_ttm,pb,dv_ratio,dv_ttm,total_share,float_share,total_mv,circ_mv",
    "dividend": "ts_code,end_date,ann_date,div_proc,stk_div,cash_div,cash_div_tax,record_date,ex_date,pay_date,imp_ann_date,base_date,base_share",
    "stk_limit": "ts_code,trade_date,pre_close,up_limit,down_limit",
    "suspend_d": "ts_code,trade_date,suspend_type,suspend_timing",
}
DATE_FIELDS = {
    "trade_date",
    "cal_date",
    "pretrade_date",
    "list_date",
    "delist_date",
    "ann_date",
    "imp_ann_date",
    "record_date",
    "ex_date",
    "pay_date",
    "end_date",
    "start_date",
    "base_date",
}
STRING_FIELDS = DATE_FIELDS | {
    "ts_code",
    "stock_code",
    "symbol",
    "cnspell",
    "name",
    "area",
    "industry",
    "market",
    "exchange",
    "list_status",
    "change_reason",
    "div_proc",
    "suspend_type",
    "suspend_timing",
}


class ProviderError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class DataProvider(Protocol):
    source: str

    def fetch(self, dataset: str, params: dict[str, str]) -> pl.DataFrame: ...

    def capabilities(self) -> dict[str, dict[str, Any]]: ...


def iso_date(value: Any) -> str | None:
    if value is None or value == "":
        return None
    text = str(value)
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}" if len(text) == 8 and text.isdigit() else text


def filter_a_share_market(frame: pl.DataFrame) -> pl.DataFrame:
    """Remove known out-of-scope securities; malformed codes still require validation."""
    if frame.is_empty() or "ts_code" not in frame.columns:
        return frame
    code = pl.col("ts_code").cast(pl.String)
    known_code = code.str.contains(r"^\d{6}\.(SH|SZ|BJ)$").fill_null(False)
    a_share = code.str.contains(
        r"^(?:(?:600|601|603|605|688)\d{3}\.SH|(?:000|001|002|003|300|301)\d{3}\.SZ)$"
    ).fill_null(False)
    return frame.filter(~known_code | a_share)


def normalize(dataset: str, frame: pl.DataFrame, source: str, batch_id: str) -> pl.DataFrame:
    expected = FIELDS[dataset].split(",")
    if frame.height and any(
        field not in frame.columns
        for field in expected
        if field in {"ts_code", "trade_date", "cal_date", "close", "adj_factor"}
    ):
        raise ProviderError("schema_error", f"{dataset} 缺少关键字段")
    rows = frame.to_dicts()
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        result: dict[str, Any] = {}
        for field in expected:
            value = row.get(field)
            if field in DATE_FIELDS:
                value = iso_date(value)
                if value:
                    try:
                        datetime.strptime(value, "%Y-%m-%d")
                    except ValueError as exc:
                        raise ProviderError("schema_error", f"{dataset} 日期格式错误") from exc
            elif field not in STRING_FIELDS and value is not None:
                try:
                    value = float(value)
                    if not math.isfinite(value):
                        raise ValueError("non-finite")
                except (ValueError, TypeError) as exc:
                    raise ProviderError("schema_error", f"{dataset} 数值字段错误") from exc
            result["stock_code" if field == "ts_code" else field] = value
        if "stock_code" in result and (
            not isinstance(result["stock_code"], str)
            or not re.fullmatch(
                r"(?:\d{6}\.(SH|SZ|BJ)|T\d{6}\.(SH|SZ))"
                if dataset == "stock_basic"
                else r"\d{6}\.(SH|SZ|BJ)",
                result["stock_code"],
            )
        ):
            raise ProviderError("schema_error", f"{dataset} 股票代码缺失或格式错误")
        for field in ["trade_date", "cal_date"]:
            if field in result and not result[field]:
                raise ProviderError("schema_error", f"{dataset} 主键日期缺失")
        if dataset == "daily":
            volume = result.pop("vol")
            result["volume"] = None if volume is None else volume * 100
            result["amount"] = None if result["amount"] is None else result["amount"] * 1000
            change = result.pop("pct_chg")
            result["daily_return"] = None if change is None else change / 100
            if any(
                result[field] is not None and result[field] < 0 for field in ["volume", "amount"]
            ):
                raise ProviderError("schema_error", "daily 成交量或成交额为负数")
            invalid_prices = [
                f
                for f in ["open", "high", "low", "close", "pre_close"]
                if result.get(f) is None or result[f] <= 0
            ]
            if invalid_prices:
                raise ProviderError(
                    "schema_error",
                    f"daily 价格缺失或非正数：{result['stock_code']} {result['trade_date']} "
                    f"字段 {','.join(invalid_prices)}",
                )
            if result["high"] < max(result["open"], result["close"], result["low"]) or result[
                "low"
            ] > min(result["open"], result["close"]):
                raise ProviderError("schema_error", "daily OHLC 不一致")
        if dataset == "daily_basic":
            for field in ["turnover_rate", "dv_ratio", "dv_ttm"]:
                if result[field] is not None:
                    result[field] /= 100
            for field in ["total_share", "float_share", "total_mv", "circ_mv"]:
                if result[field] is not None:
                    result[field] *= 10000
        result.update(
            source=source,
            batch_id=batch_id,
            schema_version=1,
            fetched_at=datetime.now(UTC).isoformat(),
        )
        cleaned.append(result)
    schema: dict[str, Any] = {}
    for field in expected:
        name = "stock_code" if field == "ts_code" else field
        if dataset == "daily" and field in {"vol", "pct_chg"}:
            continue
        schema[name] = pl.String if field in STRING_FIELDS else pl.Float64
    if dataset == "daily":
        schema.update(volume=pl.Float64, daily_return=pl.Float64)
    schema.update(
        source=pl.String, batch_id=pl.String, schema_version=pl.Int64, fetched_at=pl.String
    )
    return pl.DataFrame(cleaned, schema=schema, strict=True)


class TushareProvider:
    source = "tushare"

    def capabilities(self) -> dict[str, dict[str, Any]]:
        # Describes adapter support, not remote account permission.
        return {
            dataset: {
                "supported": True,
                "access": "unknown_until_requested",
                "row_guard_limit": limit,
            }
            for dataset, limit in LIMITS.items()
        }

    def __init__(
        self,
        settings: Settings,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.settings = settings
        self.client = client or httpx.Client(timeout=30)
        self.sleep = sleep
        self.last_call = 0.0
        self.rate_lock = threading.Lock()

    def fetch(self, dataset: str, params: dict[str, str]) -> pl.DataFrame:
        token = self.settings.tushare_token.get_secret_value()
        if not token:
            raise ProviderError("token_missing", "未配置 TUSHARE_TOKEN，不能同步真实数据")
        if dataset not in FIELDS:
            raise ProviderError("unsupported_dataset", "不支持的数据集")
        for attempt in range(4):
            with self.rate_lock:
                delay = 60 / self.settings.requests_per_minute - (time.monotonic() - self.last_call)
                if delay > 0:
                    self.sleep(delay)
                self.last_call = time.monotonic()
            try:
                response = self.client.post(
                    self.settings.tushare_http_url,
                    json={
                        "api_name": dataset,
                        "token": token,
                        "params": params,
                        "fields": FIELDS[dataset],
                    },
                )
                if response.status_code in {401, 403}:
                    raise ProviderError("permission_denied", f"{dataset} 认证或权限不足")
                if response.status_code == 429 or response.status_code >= 500:
                    raise ProviderError("temporary_error", f"{dataset} 限流或服务暂时不可用")
                response.raise_for_status()
                body = response.json()
                if body.get("code") != 0:
                    message = str(body.get("msg", ""))
                    if any(word in message for word in ["权限", "积分", "token", "TOKEN", "认证"]):
                        raise ProviderError(
                            "permission_denied", f"{dataset} 权限不足或 Token 无效，请检查账号授权"
                        )
                    if any(word in message for word in ["频", "限流", "每分钟", "每小时", "每天"]):
                        raise ProviderError("temporary_error", f"{dataset} 请求额度受限")
                    raise ProviderError(
                        "provider_error", f"{dataset} 数据源返回错误（未记录原始响应）"
                    )
                data = body.get("data")
                if (
                    not isinstance(data, dict)
                    or not isinstance(data.get("fields"), list)
                    or not isinstance(data.get("items"), list)
                ):
                    raise ProviderError("schema_error", f"{dataset} 响应结构不合法")
                return pl.DataFrame(
                    data["items"], schema=data["fields"], orient="row", infer_schema_length=None
                )
            except (httpx.TimeoutException, httpx.NetworkError):
                error = ProviderError("temporary_error", f"{dataset} 请求超时或网络失败")
            except (ValueError, httpx.HTTPStatusError):
                raise ProviderError("provider_error", f"{dataset} HTTP 或 JSON 响应异常") from None
            except ProviderError as exc:
                if exc.code != "temporary_error":
                    raise
                error = exc
            if attempt == 3:
                raise error
            self.sleep(2**attempt)
        raise AssertionError("unreachable")
