import csv
import io
import json
import shutil
from collections import OrderedDict
from copy import deepcopy
from datetime import date, datetime, timedelta
from threading import Lock, RLock
from typing import Any
from zoneinfo import ZoneInfo

import polars as pl
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.dialects.sqlite import insert

from ashare_lab.backtest_service import BacktestService
from ashare_lab.company import CompanyService
from ashare_lab.config import Settings
from ashare_lab.db import (
    Database,
    checkpoints,
    identifier,
    jobs,
    logs,
    now,
    presets,
    rule_definitions,
    settings_table,
)
from ashare_lab.dca_service import DcaService
from ashare_lab.dividends import DIVIDEND_VERSION
from ashare_lab.factors import (
    BOOLEAN_FACTORS,
    FACTOR_VERSION,
    NUMERIC_FACTORS,
    FactorEngine,
    UniverseManager,
)
from ashare_lab.instrument_search import search_rank
from ashare_lab.portfolio_service import PortfolioBacktestService
from ashare_lab.rules import RULE_VERSION, Common, RuleRegistry
from ashare_lab.scheduler import (
    SYNC_POLICY,
    DailyDividendScheduler,
    DailyIncrementalScheduler,
    DailyNameScheduler,
    MonthlyDividendScheduler,
    MonthlyNameScheduler,
)
from ashare_lab.schemas import PresetRequest, ScreeningRequest, SyncRequest, Universe
from ashare_lab.signal_screening import SIGNAL_SCREENING_VERSION, strategy_version
from ashare_lab.warehouse import MarketRepository


class ServiceError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status, self.code, self.details = status, code, details


class ApplicationService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = Database(settings.database_path)
        self.db.initialize()
        self.repository = MarketRepository(self.db, settings.data_dir)
        self._market_cache: OrderedDict[tuple[tuple[str, str], ...], dict[str, Any]] = OrderedDict()
        self._market_cache_lock = RLock()
        self._market_json_cache: OrderedDict[tuple[tuple[str, str], ...], bytes] = OrderedDict()
        self._dataset_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._dataset_cache_lock = Lock()
        self.registry = RuleRegistry()
        self.dca = DcaService(self.db, self.repository)
        self.backtest = BacktestService(self.db, self.repository)
        self.portfolio = PortfolioBacktestService(self.db, self.repository)
        self.company = CompanyService(settings, self.db, self.repository)
        self._instrument_cache: OrderedDict[tuple[str, str], tuple[dict[str, Any], ...]] = (
            OrderedDict()
        )
        self._instrument_cache_lock = Lock()
        with self.db.engine.begin() as conn:
            public_config = {
                "timezone": settings.tz,
                "market": "沪深A股",
                "default_history_years": 3,
                "requests_per_minute": settings.requests_per_minute,
            }
            conn.execute(
                insert(settings_table)
                .values(key="runtime_config", value=public_config)
                .on_conflict_do_update(index_elements=["key"], set_={"value": public_config})
            )
            for rule in self.registry.describe():
                conn.execute(
                    insert(rule_definitions)
                    .values(id=rule["id"], version=rule["version"], definition=rule)
                    .on_conflict_do_update(
                        index_elements=["id"], set_={"definition": rule, "version": rule["version"]}
                    )
                )

    def validate_rule(self, request: ScreeningRequest) -> ScreeningRequest:
        try:
            if request.mode in {"signal", "dividend"}:
                parameters = Common.model_validate(request.parameters)
            else:
                assert request.rule_id is not None
                parameters = self.registry.validate(request.rule_id, request.parameters)
        except ValidationError as exc:
            raise ServiceError(
                422,
                "invalid_parameters",
                "规则参数不合法",
                exc.errors(include_url=False, include_context=False, include_input=False),
            ) from exc
        return request.model_copy(update={"parameters": parameters.model_dump(mode="json")})

    def submit_screen(self, request: ScreeningRequest) -> dict[str, Any]:
        request = self.validate_rule(request)
        snapshot = self.db.snapshot()
        if not {"stock_basic", "trade_cal", "daily"} <= snapshot.keys():
            raise ServiceError(409, "data_not_ready", "请先同步基础信息、交易日历和行情")
        day = request.as_of_date.isoformat()
        if day not in FactorEngine(self.repository).trading_days(day, snapshot):
            raise ServiceError(422, "not_trading_day", "指定日期不是已同步的交易日")
        if not self.repository.covered_date("daily", day, snapshot):
            raise ServiceError(409, "day_not_synced", "指定交易日未完成行情同步")
        signal_versions = {}
        if request.signal_filter:
            if not {"adj_factor", "suspend_d"} <= snapshot.keys():
                raise ServiceError(409, "data_not_ready", "策略信号需要复权因子和停复牌数据版本")
            signal_versions = {
                "_signal_version": SIGNAL_SCREENING_VERSION,
                **{
                    f"_strategy_version_{config.id}": strategy_version(config)
                    for config in request.signal_filter.strategies
                },
            }
        return self.db.enqueue(
            "screening",
            request.model_dump(mode="json"),
            {
                **snapshot,
                "_rule_version": RULE_VERSION,
                "_factor_version": FACTOR_VERSION,
                "_universe_policy": "survivors_v1",
                "_dividend_version": DIVIDEND_VERSION,
                **signal_versions,
            },
        )

    def submit_sync(self, request: SyncRequest) -> dict[str, Any]:
        payload = request.model_dump(mode="json")
        if request.mode in {"dividend", "namechange"}:
            payload["history_refresh"] = request.history_refresh or "incremental"
            payload["end_date"] = (
                payload["end_date"]
                or (datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)).isoformat()
            )
        return self.db.enqueue(
            "sync",
            payload,
            {**self.db.snapshot(), "_sync_policy": SYNC_POLICY},
        )

    def job(self, job_id: str, kind: str | None = None) -> dict[str, Any]:
        try:
            job = self.db.job(job_id)
        except KeyError as exc:
            raise ServiceError(404, "not_found", "任务不存在") from exc
        if kind and job["kind"] != kind:
            raise ServiceError(404, "not_found", "该类型任务不存在")
        return job

    def task_list(
        self, kind: str | None = None, limit: int = 20, offset: int = 0
    ) -> list[dict[str, Any]]:
        with self.db.engine.connect() as conn:
            query = (
                select(jobs)
                .order_by(jobs.c.created_at.desc(), jobs.c.id.desc())
                .limit(limit)
                .offset(offset)
            )
            if kind:
                query = query.where(jobs.c.kind == kind)
            return [dict(row) for row in conn.execute(query).mappings()]

    def _cleanup_job_results(self, job_id: str) -> None:
        results_root = (self.repository.root / "results").resolve()
        directory = results_root / job_id
        if directory.parent != results_root or directory.is_symlink():
            raise ServiceError(503, "cleanup_failed", "结果目录异常，任务未删除")
        try:
            shutil.rmtree(directory)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise ServiceError(503, "cleanup_failed", "结果文件清理失败，请重试删除") from exc

    def delete_jobs(self, kind: str) -> dict[str, Any]:
        """Delete all finished history of one module under the same worker lock."""
        with self.db.engine.connect() as conn:
            conn.execute(text("BEGIN IMMEDIATE"))
            try:
                rows = conn.execute(
                    select(jobs.c.id, jobs.c.status).where(jobs.c.kind == kind)
                ).all()
                ids = [
                    row.id
                    for row in rows
                    if row.status in {"succeeded", "failed", "cancelled", "partial"}
                ]
                for job_id in ids:
                    self._cleanup_job_results(job_id)
                if ids:
                    conn.execute(logs.delete().where(logs.c.job_id.in_(ids)))
                    conn.execute(checkpoints.delete().where(checkpoints.c.job_id.in_(ids)))
                    conn.execute(jobs.delete().where(jobs.c.id.in_(ids)))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {"deleted": True, "ids": ids, "skipped_active": len(rows) - len(ids)}

    def delete_job(self, job_id: str, kind: str) -> dict[str, Any]:
        with self.db.engine.connect() as conn:
            conn.execute(text("BEGIN IMMEDIATE"))
            try:
                job = conn.execute(select(jobs).where(jobs.c.id == job_id)).mappings().first()
                if job is None or job["kind"] != kind:
                    raise ServiceError(404, "not_found", "该类型任务不存在")
                if job["status"] not in {"succeeded", "failed", "cancelled", "partial"}:
                    raise ServiceError(409, "task_active", "请先取消任务，等待任务结束后再删除")
                self._cleanup_job_results(job_id)
                conn.execute(logs.delete().where(logs.c.job_id == job_id))
                conn.execute(checkpoints.delete().where(checkpoints.c.job_id == job_id))
                conn.execute(jobs.delete().where(jobs.c.id == job_id))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {"deleted": True, "id": job_id}

    def cancel(self, job_id: str, kind: str) -> dict[str, Any]:
        with self.db.engine.begin() as conn:
            conn.execute(text("BEGIN IMMEDIATE"))
            job = conn.execute(select(jobs).where(jobs.c.id == job_id)).mappings().first()
            if job is None or job["kind"] != kind:
                raise ServiceError(404, "not_found", "该类型任务不存在")
            if job["status"] not in {"queued", "running", "cancel_requested"}:
                raise ServiceError(409, "task_finished", "任务已结束")
            conn.execute(
                jobs.update()
                .where(jobs.c.id == job_id)
                .values(
                    cancel_requested=True,
                    status="cancelled" if job["status"] == "queued" else "cancel_requested",
                    updated_at=now(),
                )
            )
        return self.job(job_id, kind)

    def resume(self, job_id: str) -> dict[str, Any]:
        with self.db.engine.connect() as conn:
            conn.execute(text("BEGIN IMMEDIATE"))
            try:
                job = conn.execute(select(jobs).where(jobs.c.id == job_id)).mappings().first()
                if job is None or job["kind"] != "sync":
                    raise ServiceError(404, "not_found", "同步任务不存在")
                if job["status"] not in {"failed", "cancelled", "partial"}:
                    raise ServiceError(409, "cannot_resume", "仅失败、取消或降级的同步任务可恢复")
                conn.execute(
                    jobs.update()
                    .where(jobs.c.id == job_id)
                    .values(status="queued", cancel_requested=False, error=None, updated_at=now())
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return self.job(job_id, "sync")

    def datasets(self, snapshot: dict[str, str] | None = None) -> list[dict[str, Any]]:
        output = []
        snapshot = self.db.snapshot() if snapshot is None else snapshot
        for dataset, version_id in sorted(snapshot.items()):
            with self._dataset_cache_lock:
                if version_id not in self._dataset_cache:
                    version = self.repository.version(version_id)
                    self._dataset_cache[version_id] = {
                        "dataset": dataset,
                        "version_id": version_id,
                        "source": version["source"],
                        "updated_at": version["created_at"],
                        **self.repository.summary(version_id),
                        "coverage": version["coverage"],
                        "partitions": len(version["manifest"]),
                    }
                    if len(self._dataset_cache) > 32:
                        self._dataset_cache.popitem(last=False)
                self._dataset_cache.move_to_end(version_id)
                output.append(deepcopy(self._dataset_cache[version_id]))
        return output

    def market_facts(self, snapshot: dict[str, str], *, copy_result: bool = True) -> dict[str, Any]:
        key = tuple(sorted(snapshot.items()))
        with self._market_cache_lock:
            if key not in self._market_cache:
                datasets = self.datasets(snapshot)
                daily = next((d for d in datasets if d["dataset"] == "daily"), None)
                latest = daily["end_date"] if daily else None
                missing_dates = []
                if daily and daily["start_date"] and latest:
                    covered_days = {
                        day
                        for entry in daily["coverage"].values()
                        if entry.get("complete")
                        for day in (
                            [entry["trade_date"]]
                            if entry.get("trade_date")
                            else entry.get("trade_dates", [])
                        )
                    }
                    missing_dates = [
                        day
                        for day in FactorEngine(self.repository).trading_days(latest, snapshot)
                        if day >= daily["start_date"] and day not in covered_days
                    ]
                self._market_cache[key] = {
                    "datasets": datasets,
                    "latest_market_date": latest,
                    "missing_trade_dates": missing_dates,
                    "stock_count": len(self.instrument_rows(snapshot["stock_basic"], latest))
                    if latest and "stock_basic" in snapshot
                    else 0,
                    "fixture_mode": any(d["source"] == "fixture" for d in datasets),
                }
                if len(self._market_cache) > 4:
                    self._market_cache.popitem(last=False)
            self._market_cache.move_to_end(key)
            return deepcopy(self._market_cache[key]) if copy_result else self._market_cache[key]

    def market_status(self, management: bool = True) -> dict[str, Any]:
        snapshot = self.db.snapshot()
        facts = self.market_facts(snapshot)
        return {**facts, **self.market_metadata(snapshot, facts["datasets"], management)}

    def market_status_json(self, management: bool = True) -> bytes:
        snapshot = self.db.snapshot()
        key = tuple(sorted(snapshot.items()))
        with self._market_cache_lock:
            facts = self.market_facts(snapshot, copy_result=False)
            if key not in self._market_json_cache:
                self._market_json_cache[key] = json.dumps(
                    facts, ensure_ascii=False, allow_nan=False, separators=(",", ":")
                ).encode()
                if len(self._market_json_cache) > 4:
                    self._market_json_cache.popitem(last=False)
            self._market_json_cache.move_to_end(key)
            encoded = self._market_json_cache[key]
        metadata = self.market_metadata(snapshot, facts["datasets"], management)
        dynamic = json.dumps(
            metadata, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
        # Only immutable facts are serialized once; jobs and schedules stay live.
        return encoded[:-1] + b"," + dynamic[1:]

    def market_metadata(
        self, snapshot: dict[str, str], datasets: list[dict[str, Any]], management: bool
    ) -> dict[str, Any]:
        with self.db.engine.connect() as conn:
            public_config: dict[str, Any] = conn.execute(
                select(settings_table.c.value).where(settings_table.c.key == "runtime_config")
            ).scalar_one()
        latest_sync = self.task_list("sync", 1)
        return {
            "system_config": public_config,
            "incremental_schedule": DailyIncrementalScheduler(
                self.settings, self.db, self.repository
            ).status(snapshot=snapshot, datasets=datasets),
            "dividend_schedule": DailyDividendScheduler(
                self.settings, self.db, self.repository
            ).status(snapshot=snapshot, datasets=datasets),
            "namechange_schedule": DailyNameScheduler(
                self.settings, self.db, self.repository
            ).status(snapshot=snapshot, datasets=datasets),
            "namechange_full_schedule": MonthlyNameScheduler(
                self.settings, self.db, self.repository
            ).status(snapshot=snapshot, datasets=datasets),
            "dividend_full_schedule": MonthlyDividendScheduler(
                self.settings, self.db, self.repository
            ).status(snapshot=snapshot, datasets=datasets),
            "universe_policy": "排除数据版本中已退市证券；历史结果仅涵盖存续证券",
            "token_configured": bool(self.settings.tushare_token.get_secret_value()),
            "recent_screening": self.task_list("screening", 5),
            "recent_sync": self.task_list("sync", 5) if management else [],
            "latest_sync_status": latest_sync[0]["status"] if latest_sync else None,
            "pit_limitation": "公告日期保守重建；未重建供应商历史修订快照",
        }

    def instrument_rows(self, version: str, day: str) -> tuple[dict[str, Any], ...]:
        key = (version, day)
        with self._instrument_cache_lock:
            if key not in self._instrument_cache:
                self._instrument_cache[key] = tuple(
                    UniverseManager(self.repository).resolve(
                        day, Universe(), {"stock_basic": version}
                    )
                )
                if len(self._instrument_cache) > 8:
                    self._instrument_cache.popitem(last=False)
            self._instrument_cache.move_to_end(key)
            return self._instrument_cache[key]

    def instruments(
        self,
        as_of: str | None,
        search: str,
        page: int,
        page_size: int,
        data_version: str | None = None,
    ) -> dict[str, Any]:
        version_id = data_version or self.db.snapshot().get("stock_basic")
        if version_id is None:
            return {"items": [], "total": 0, "page": page, "page_size": page_size}
        try:
            version = self.repository.version(version_id, resolve_coverage=False)
        except KeyError as exc:
            raise ServiceError(422, "invalid_version", "股票基础信息版本不存在") from exc
        if version["dataset"] != "stock_basic":
            raise ServiceError(422, "invalid_version", "联想必须使用股票基础信息版本")
        # Display search does not need to scan all market datasets on each keystroke.
        day = as_of or datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        ranked = [
            (rank, row)
            for row in self.instrument_rows(version_id, day)
            if (rank := search_rank(row, search)) is not None
        ]
        ranked.sort(key=lambda item: (item[0], item[1]["stock_code"]))
        rows = [row for _, row in ranked]
        return {
            "items": rows[(page - 1) * page_size : page * page_size],
            "total": len(rows),
            "page": page,
            "page_size": page_size,
            "data_version": version_id,
        }

    def results(
        self,
        job_id: str,
        match_status: str,
        search: str,
        sort_by: str,
        order: str,
        page: int,
        page_size: int,
        filter_factor: str | None = None,
        min_value: float | None = None,
        max_value: float | None = None,
    ) -> dict[str, Any]:
        job = self.job(job_id, "screening")
        if job["status"] != "succeeded":
            raise ServiceError(409, "results_not_ready", "筛选尚未完成，没有已发布结果")
        if sort_by not in NUMERIC_FACTORS | BOOLEAN_FACTORS | {
            "stock_code": (),
            "stock_name": (),
            "latest_trade_date": (),
            "record_date": (),
            "cash_per_10": (),
            "estimated_cash_10000": (),
        }:
            raise ServiceError(422, "invalid_sort", "不支持该排序字段")
        rows = pl.read_parquet(self.settings.data_dir / job["result"]["path"]).to_dicts()
        for row in rows:
            for key in [
                "factor_values",
                "data_quality_flags",
                "condition_results",
                "matched_factors",
                *(["signal_results"] if "signal_results" in row else []),
                *(["recent_dividend"] if "recent_dividend" in row else []),
            ]:
                row[key] = json.loads(row[key])
        dates = sorted({r["latest_trade_date"] for r in rows if r.get("latest_trade_date")})
        summary = {
            **job["result"],
            "as_of_date": job["payload"]["as_of_date"],
            "effective_market_dates": dates,
            "missing_market_dates": sum(not r.get("latest_trade_date") for r in rows),
        }
        rows = [
            r
            for r in rows
            if (match_status == "all" or r["match_status"] == match_status)
            and search_rank(r, search) is not None
        ]
        if filter_factor is not None:
            if filter_factor not in NUMERIC_FACTORS:
                raise ServiceError(422, "invalid_filter", "只支持白名单数值因子筛选")
            if min_value is not None and max_value is not None and min_value > max_value:
                raise ServiceError(422, "invalid_filter", "筛选下限不能大于上限")
            rows = [
                r
                for r in rows
                if r["factor_values"].get(filter_factor) is not None
                and (min_value is None or r["factor_values"][filter_factor] >= min_value)
                and (max_value is None or r["factor_values"][filter_factor] <= max_value)
            ]

        def sort_value(row: dict[str, Any]) -> Any:
            if sort_by in {"record_date", "cash_per_10", "estimated_cash_10000"}:
                events = (row.get("recent_dividend") or {}).get("events", [])
                return events[0].get(sort_by) if events else None
            return row.get(sort_by) if sort_by in row else row["factor_values"].get(sort_by)

        valid = sorted(
            [r for r in rows if sort_value(r) is not None], key=lambda r: r["stock_code"]
        )
        valid.sort(key=sort_value, reverse=order == "desc")
        ordered = valid + sorted(
            [r for r in rows if sort_value(r) is None], key=lambda r: r["stock_code"]
        )
        return {
            "items": ordered[(page - 1) * page_size : page * page_size],
            "total": len(ordered),
            "page": page,
            "page_size": page_size,
            "summary": summary,
            "data_versions": job["snapshot"],
        }

    def export(self, job_id: str) -> str:
        rows = self.results(job_id, "matched", "", "stock_code", "asc", 1, 10000)["items"]
        stream = io.StringIO()
        writer = csv.writer(stream)
        columns = ["stock_code", "stock_name", "as_of_date", "latest_trade_date", "rule_id"]
        factors = list(NUMERIC_FACTORS)
        writer.writerow(
            columns
            + [
                f"{name} ({NUMERIC_FACTORS[name][1]};百分比为小数)"
                if NUMERIC_FACTORS[name][1] == "%"
                else f"{name} ({NUMERIC_FACTORS[name][1]})"
                for name in factors
            ]
            + [
                "data_versions",
                "data_quality_flags",
                "universe_policy",
                "signal_results",
                "recent_dividend (税前;金额比例估算)",
                "fixture_mode",
            ]
        )

        def safe(value: Any) -> Any:
            if isinstance(value, str) and value.lstrip().startswith(
                ("=", "+", "-", "@", "\t", "\r")
            ):
                return "'" + value
            return value

        snapshot = self.job(job_id)["snapshot"]
        fixture_mode = any(
            self.repository.version(version, resolve_coverage=False)["source"] == "fixture"
            for key, version in snapshot.items()
            if not key.startswith("_")
        )
        for row in rows:
            writer.writerow(
                [safe(row.get(k)) for k in columns]
                + [row["factor_values"].get(k) for k in factors]
                + [
                    json.dumps(snapshot, sort_keys=True),
                    ";".join(row["data_quality_flags"]),
                    "排除数据版本中已退市证券"
                    if snapshot.get("_universe_policy") == "survivors_v1"
                    else "历史上市股票池",
                    json.dumps(row.get("signal_results", []), ensure_ascii=False, allow_nan=False),
                    json.dumps(row.get("recent_dividend"), ensure_ascii=False, allow_nan=False),
                    fixture_mode,
                ]
            )
        return "\ufeff" + stream.getvalue()

    def save_preset(self, request: PresetRequest) -> dict[str, Any]:
        definition = self.validate_rule(request.definition)
        row = {
            "id": identifier(),
            "name": request.name,
            "definition": definition.model_dump(mode="json"),
            "created_at": now(),
        }
        with self.db.engine.begin() as conn:
            conn.execute(presets.insert().values(**row))
        return row

    def list_presets(self) -> list[dict[str, Any]]:
        with self.db.engine.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    select(presets).order_by(presets.c.created_at.desc())
                ).mappings()
            ]

    def bars(
        self, code: str, start: date | None, end: date | None, adjustment: str, run_id: str | None
    ) -> dict[str, Any]:
        job = self.job(run_id, "screening") if run_id else None
        snapshot = job["snapshot"] if job else self.db.snapshot()
        cutoff = date.fromisoformat(job["payload"]["as_of_date"]) if job else date.today()
        end = end or cutoff
        if end > cutoff:
            raise ServiceError(422, "future_bars", "不能请求筛选日期之后的行情")
        start = start or end - timedelta(days=365)
        if start > end or (end - start).days > 2000:
            raise ServiceError(422, "invalid_range", "日期范围无效或超过2000个日历日")
        loaded_start = start - timedelta(days=120)
        raw = self.repository.read(
            "daily", snapshot, [code], loaded_start.isoformat(), end.isoformat()
        )
        if raw.is_empty():
            return {
                "items": [],
                "adjustment": adjustment,
                "data_versions": snapshot,
                "instrument": None,
            }
        raw = raw.sort("trade_date")
        adj = self.repository.read(
            "adj_factor", snapshot, [code], loaded_start.isoformat(), end.isoformat()
        )
        anchor_date = raw["trade_date"][-1]
        anchor = (
            adj.filter(pl.col("trade_date") == anchor_date)["adj_factor"].item()
            if not adj.is_empty() and adj.filter(pl.col("trade_date") == anchor_date).height
            else None
        )
        if adjustment == "asof":
            if not anchor or anchor <= 0:
                raise ServiceError(409, "adjustment_missing", "复权因子缺失，无法绘制信号价格图")
            raw = raw.join(adj.select("trade_date", "adj_factor"), on="trade_date", how="left")
            if raw["adj_factor"].null_count():
                raise ServiceError(409, "adjustment_missing", "历史复权因子不完整")
            raw = raw.with_columns(
                (pl.col(field) * pl.col("adj_factor") / anchor).alias(field)
                for field in ["open", "high", "low", "close"]
            )
        calendar = self.repository.read(
            "trade_cal", snapshot, start_date=loaded_start.isoformat(), end_date=end.isoformat()
        )
        days = (
            calendar.filter(pl.col("is_open") == 1)
            .select(pl.col("cal_date").alias("trade_date"))
            .unique()
            .sort("trade_date")
        )
        grid = days.join(raw, on="trade_date", how="left").with_columns(
            pl.col("close").rolling_mean(20, min_samples=20).alias("ma20"),
            pl.col("close").rolling_mean(60, min_samples=60).alias("ma60"),
        )
        selected = grid.filter(
            (pl.col("trade_date") >= start.isoformat()) & pl.col("close").is_not_null()
        ).select("trade_date", "open", "high", "low", "close", "volume", "ma20", "ma60")
        instrument = self.repository.read("stock_basic", snapshot, [code])
        return {
            "items": selected.to_dicts(),
            "adjustment": adjustment,
            "anchor_date": anchor_date,
            "data_versions": snapshot,
            "instrument": instrument.to_dicts()[0] if instrument.height else None,
            "units": {"price": "元", "volume": "股"},
        }
