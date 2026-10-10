import json
from collections import deque
from collections.abc import Callable, Generator, Iterable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor, wait
from contextlib import closing
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import polars as pl

from ashare_lab.db import Database
from ashare_lab.factors import UniverseManager
from ashare_lab.providers import (
    LIMITS,
    DataProvider,
    ProviderError,
    filter_a_share_market,
    normalize,
)
from ashare_lab.scheduler import SURVIVOR_SYNC_POLICIES, SYNC_POLICY
from ashare_lab.schemas import SyncRequest, Universe
from ashare_lab.warehouse import MarketRepository

OPTIONAL = {"stk_limit", "suspend_d", "namechange", "daily_basic", "adj_factor", "dividend"}


def prefetch[T, R](
    request: Callable[[T], R], items: Iterable[T], concurrency: int
) -> Generator[tuple[T, Future[R]], None, None]:
    """Bound outstanding responses; the caller alone validates and publishes them."""
    pending: deque[tuple[T, Future[R]]] = deque()
    iterator = iter(items)
    pool = ThreadPoolExecutor(max_workers=concurrency)
    try:
        for _ in range(concurrency):
            item = next(iterator, None)
            if item is None:
                break
            pending.append((item, pool.submit(request, item)))
        while pending:
            item, future = pending.popleft()
            wait([future])
            yield item, future
            item = next(iterator, None)
            if item is not None:
                pending.append((item, pool.submit(request, item)))
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


class SyncService:
    def __init__(
        self,
        db: Database,
        repository: MarketRepository,
        provider: DataProvider,
        concurrency: int = 1,
    ):
        self.db, self.repository, self.provider = db, repository, provider
        self.concurrency = concurrency

    def run(
        self,
        job: dict[str, Any],
        check: Callable[[], None],
        *,
        before_complete: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        request = SyncRequest.model_validate(job["payload"])
        policy = job["snapshot"].get("_sync_policy")
        modern = policy in SURVIVOR_SYNC_POLICIES
        history_only = request.mode in {"namechange", "dividend"}
        if self.provider.source == "tushare" and any(
            self.repository.version(v)["source"] == "fixture" for v in self.db.snapshot().values()
        ):
            raise ProviderError(
                "fixture_isolation", "测试数据目录不能混入真实行情，请切换 DATA_DIR/DATABASE_PATH"
            )
        now_local = datetime.now(ZoneInfo("Asia/Shanghai"))
        completed_date = (
            now_local.date() if now_local.hour >= 22 else now_local.date() - timedelta(days=1)
        )
        end = request.end_date or (
            (now_local.date() - timedelta(days=1) if policy == SYNC_POLICY else now_local.date())
            if history_only
            else completed_date
        )
        if history_only:
            start = end
        elif request.mode == "initialize":
            assert request.start_date is not None
            start = request.start_date - timedelta(days=120)
        else:
            latest = self.repository.latest_date("daily", self.db.snapshot())
            if latest is None:
                raise ProviderError("needs_initialization", "尚无行情，请先初始化历史数据")
            start = datetime.strptime(latest, "%Y-%m-%d").date() - timedelta(days=10)
        if start > end:
            raise ProviderError("invalid_range", "同步起止日期无效")
        job_id = job["id"]
        warnings: list[str] = []
        unavailable: set[str] = set()

        def fetch(
            dataset: str,
            params: dict[str, str],
            key: str,
            info: dict[str, Any] | None = None,
            response: Future[pl.DataFrame] | None = None,
        ) -> pl.DataFrame | None:
            check()
            if dataset in unavailable:
                return None
            if self.db.completed(job_id, f"{dataset}:{key}"):
                return None
            try:
                frame = (
                    response.result()
                    if response is not None
                    else self.provider.fetch(dataset, params)
                )
                if frame.height >= LIMITS[dataset]:
                    raise ProviderError("truncated", f"{dataset} 返回达到行数上限，不能视为完整")
                if "trade_date" in params and "ts_code" not in params:
                    raw_count = frame.height
                    frame = filter_a_share_market(frame)
                    if frame.height != raw_count:
                        self.db.log(
                            job_id,
                            "info",
                            f"{dataset} {key}: 排除 {raw_count - frame.height} 行范围外证券",
                        )
                if dataset == "daily" and not frame.height and "ts_code" not in params:
                    raise ProviderError(
                        "empty_market_day", f"{params.get('trade_date')} 日线为空，可能尚未入库"
                    )
                normalized = normalize(dataset, frame, self.provider.source, job_id)
                if (
                    params.get("ts_code")
                    and normalized.height
                    and "stock_code" in normalized.columns
                ):
                    if normalized.filter(pl.col("stock_code") != params["ts_code"]).height:
                        raise ProviderError("schema_error", f"{dataset} 返回股票与请求不一致")
                if dataset == "trade_cal":
                    first = datetime.strptime(params["start_date"], "%Y%m%d").date()
                    last = datetime.strptime(params["end_date"], "%Y%m%d").date()
                    expected = {
                        (first + timedelta(days=offset)).isoformat()
                        for offset in range((last - first).days + 1)
                    }
                    actual = set(normalized["cal_date"].to_list()) if normalized.height else set()
                    if (
                        actual != expected
                        or normalized.filter(~pl.col("is_open").is_in([0, 1])).height
                    ):
                        raise ProviderError("calendar_incomplete", "交易日历缺失或包含非法开市标记")
                if (
                    params.get("trade_date")
                    and normalized.height
                    and "trade_date" in normalized.columns
                ):
                    expected_day = (
                        datetime.strptime(params["trade_date"], "%Y%m%d").date().isoformat()
                    )
                    if normalized.filter(pl.col("trade_date") != expected_day).height:
                        raise ProviderError("schema_error", f"{dataset} 返回日期与请求不一致")
                self.repository.publish(
                    dataset,
                    normalized,
                    key,
                    self.provider.source,
                    job_id,
                    checkpoint=(job_id, f"{dataset}:{key}"),
                    coverage_info=info,
                )
                self.db.log(job_id, "info", f"{dataset} {key}: {frame.height} 行已发布")
                return frame
            except ProviderError as exc:
                if dataset in OPTIONAL and exc.code == "permission_denied":
                    unavailable.add(dataset)
                    warnings.append(str(exc))
                    self.db.log(job_id, "warning", str(exc))
                    return None
                raise

        def stock_history(dataset: str, stock_codes: list[str]) -> None:
            def pending_codes() -> Iterator[str]:
                for code in stock_codes:
                    check()
                    if dataset in unavailable:
                        break
                    if not self.db.completed(job_id, f"{dataset}:stock:{code}"):
                        yield code

            def request(code: str) -> pl.DataFrame:
                check()
                return self.provider.fetch(dataset, {"ts_code": code})

            responses = prefetch(request, pending_codes(), self.concurrency)
            try:
                for number, (code, response) in enumerate(responses):
                    fetch(
                        dataset,
                        {"ts_code": code},
                        f"stock:{code}",
                        {"as_of_date": end.isoformat()},
                        response,
                    )
                    if dataset in unavailable:
                        break
                    if history_only:
                        self.db.update(
                            job_id, progress=0.1 + 0.9 * (number + 1) / max(len(stock_codes), 1)
                        )
                    if dataset == "dividend" and not history_only:
                        self.db.update(
                            job_id, progress=0.75 + 0.25 * (number + 1) / max(len(stock_codes), 1)
                        )
            finally:
                responses.close()

        for status in ["L", "D", "P"]:
            try:
                fetch("stock_basic", {"list_status": status}, status)
            except ProviderError as exc:
                if exc.code != "truncated":
                    raise
                for exchange in ["SSE", "SZSE"]:
                    fetch(
                        "stock_basic",
                        {"list_status": status, "exchange": exchange},
                        f"{status}:{exchange}",
                    )
        basics = self.repository.read("stock_basic", self.db.snapshot())
        if basics.is_empty():
            raise ProviderError("instruments_empty", "股票基础信息为空，无法确认市场股票池")
        codes = [
            row["stock_code"]
            for row in UniverseManager(self.repository).resolve(
                end.isoformat(), Universe(), self.db.snapshot(), exclude_delisted=modern
            )
        ]
        all_codes = sorted(
            {
                row["stock_code"]
                for row in basics.to_dicts()
                if row.get("exchange") in {"SSE", "SZSE"}
                and row["stock_code"].startswith(("0", "3", "6"))
                and (not modern or row.get("list_status") != "D")
            }
        )
        if not modern and not history_only:
            # Historical delisted stocks must also get name and dividend histories.
            try:
                fetch(
                    "namechange",
                    {},
                    "all",
                    {"complete_codes": all_codes, "as_of_date": end.isoformat()},
                )
            except ProviderError as exc:
                if exc.code != "truncated":
                    raise
                stock_history("namechange", all_codes)
        if history_only and policy == SYNC_POLICY:
            from ashare_lab.history import HistoryRefresh

            try:
                return HistoryRefresh(
                    self.db, self.repository, self.provider, self.concurrency
                ).run(
                    job,
                    request.mode,
                    all_codes,
                    end,
                    request.history_refresh == "full",
                    check,
                    before_complete=before_complete,
                )
            except ProviderError as exc:
                if exc.code != "permission_denied":
                    raise
                self.db.log(job_id, "warning", str(exc))
                return {"warnings": [str(exc)], "datasets": self.db.snapshot()}
        if history_only:
            history_dataset = request.mode
            codes_key = f"_{history_dataset}_codes"
            date_key = f"_{history_dataset}_as_of"
            # Pin targets and observation date once; resumed jobs never acquire new targets.
            if codes_key not in job["snapshot"]:
                job["snapshot"].update(
                    {codes_key: json.dumps(all_codes), date_key: end.isoformat()}
                )
                self.db.update(job_id, snapshot=job["snapshot"])
            all_codes = json.loads(job["snapshot"][codes_key])
            end = datetime.strptime(job["snapshot"][date_key], "%Y-%m-%d").date()
            stock_history(history_dataset, all_codes)
            return {
                "warnings": warnings,
                "datasets": self.db.snapshot(),
                "end_date": end.isoformat(),
                "stock_count": len(all_codes),
            }
        cursor = start
        while cursor <= end:
            year_end = min(end, cursor.replace(month=12, day=31))
            fetch(
                "trade_cal",
                {
                    "exchange": "SSE",
                    "start_date": cursor.strftime("%Y%m%d"),
                    "end_date": year_end.strftime("%Y%m%d"),
                },
                f"{cursor}:{year_end}",
            )
            cursor = year_end + timedelta(days=1)
        calendar = self.repository.read(
            "trade_cal", self.db.snapshot(), start_date=start.isoformat(), end_date=end.isoformat()
        )
        if calendar.is_empty():
            raise ProviderError("calendar_missing", "交易日历为空，不能视为同步成功")
        days = sorted(set(calendar.filter(pl.col("is_open") == 1)["cal_date"].to_list()))
        if not days:
            raise ProviderError("calendar_missing", "同步范围内没有可确认交易日")
        for number, day in enumerate(days):
            check()
            pending_datasets = [
                dataset
                for dataset in ["daily", "adj_factor", "daily_basic", "stk_limit", "suspend_d"]
                if dataset not in unavailable and not self.db.completed(job_id, f"{dataset}:{day}")
            ]

            def request_day(dataset: str, day: str = day) -> pl.DataFrame:
                check()
                return self.provider.fetch(dataset, {"trade_date": day.replace("-", "")})

            with closing(prefetch(request_day, pending_datasets, self.concurrency)) as responses:
                for dataset, response in responses:
                    try:
                        fetch(
                            dataset,
                            {"trade_date": day.replace("-", "")},
                            day,
                            {"trade_date": day},
                            response,
                        )
                    except ProviderError as exc:
                        if exc.code != "truncated":
                            raise
                        active = UniverseManager(self.repository).resolve(
                            day, Universe(), self.db.snapshot(), exclude_delisted=modern
                        )
                        for stock in active:
                            fetch(
                                dataset,
                                {
                                    "trade_date": day.replace("-", ""),
                                    "ts_code": stock["stock_code"],
                                },
                                f"{day}:{stock['stock_code']}",
                            )
                        self.repository.publish(
                            dataset,
                            normalize(dataset, pl.DataFrame(), self.provider.source, job_id),
                            day,
                            self.provider.source,
                            job_id,
                            checkpoint=(job_id, f"{dataset}:{day}"),
                            coverage_info={"trade_date": day},
                        )
            self.db.update(
                job_id,
                progress=0.1
                + (0.85 if request.mode == "incremental" and policy == SYNC_POLICY else 0.65)
                * (number + 1)
                / len(days),
            )
        # Per-stock calls include all records, not a latest-only yield field.
        if request.mode == "initialize" or policy not in {SYNC_POLICY, "daily_dividends_v2"}:
            stock_history("dividend", all_codes)
        return {
            "warnings": warnings,
            "datasets": self.db.snapshot(),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "stock_count": len(codes),
        }
