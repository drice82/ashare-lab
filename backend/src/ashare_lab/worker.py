import fcntl
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import polars as pl
from sqlalchemy import select, text

from ashare_lab.backtest import BACKTEST_VERSION, STRATEGY_VERSION
from ashare_lab.backtest import prepare as prepare_backtest
from ashare_lab.backtest import simulate as simulate_backtest
from ashare_lab.config import Settings
from ashare_lab.db import Database, identifier, jobs, now
from ashare_lab.dca import DCA_VERSION, prepare, simulate
from ashare_lab.factors import FACTOR_VERSION
from ashare_lab.providers import DataProvider, ProviderError, TushareProvider
from ashare_lab.rules import RULE_VERSION
from ashare_lab.scheduler import (
    DailyDividendScheduler,
    DailyIncrementalScheduler,
    DailyNameScheduler,
    MonthlyDividendScheduler,
    MonthlyNameScheduler,
    create_scheduler,
)
from ashare_lab.schemas import BacktestRequest, DcaRequest, ScreeningRequest
from ashare_lab.screening import ScreeningEngine
from ashare_lab.sync import SyncService
from ashare_lab.warehouse import MarketRepository


class Cancelled(Exception):
    pass


class Worker:
    def __init__(self, settings: Settings, provider: DataProvider | None = None):
        self.settings = settings
        self.db = Database(settings.database_path)
        self.db.initialize()
        self.repository = MarketRepository(self.db, settings.data_dir)
        self.provider = provider or TushareProvider(settings)

    def check(self, job_id: str) -> None:
        if self.db.is_cancelled(job_id):
            raise Cancelled()

    def recover(self) -> None:
        with self.db.engine.begin() as conn:
            conn.execute(text("BEGIN IMMEDIATE"))
            abandoned = (
                conn.execute(select(jobs).where(jobs.c.status.in_(["running", "cancel_requested"])))
                .mappings()
                .all()
            )
            for job in abandoned:
                snapshot = dict(job["snapshot"])
                status, error = "queued", None
                if job["cancel_requested"]:
                    status = "cancelled"
                elif job["kind"] in {"screening", "dca", "backtest"}:
                    interruptions = snapshot.get("_recovery_interruptions", 0) + 1
                    snapshot["_recovery_interruptions"] = interruptions
                    if interruptions >= 3:
                        status, error = (
                            "failed",
                            "任务反复中断，已停止自动重算；请检查资源后重新提交",
                        )
                conn.execute(
                    jobs.update()
                    .where(jobs.c.id == job["id"])
                    .values(
                        status=status,
                        snapshot=snapshot,
                        error=error,
                        updated_at=now(),
                    )
                )
                self.db.log(
                    job["id"],
                    "warning",
                    error
                    or (
                        "Worker 重启：取消任务"
                        if status == "cancelled"
                        else "Worker 重启：从持久检查点恢复，研究任务使用原数据版本"
                    ),
                    connection=conn,
                )

    def run_once(self) -> bool:
        with self.db.engine.begin() as conn:
            conn.execute(text("BEGIN IMMEDIATE"))
            row = (
                conn.execute(
                    select(jobs)
                    .where(jobs.c.status == "queued")
                    .order_by(jobs.c.created_at)
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if row is None:
                return False
            job = dict(row)
            conn.execute(
                jobs.update()
                .where(jobs.c.id == job["id"], jobs.c.status == "queued")
                .values(
                    status="running", heartbeat=now(), updated_at=now(), attempt=job["attempt"] + 1
                )
            )
        job_id = job["id"]
        stopped = threading.Event()

        def heartbeat() -> None:
            while not stopped.wait(2):
                self.db.update(job_id, heartbeat=now())

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()

        def finish(status: str, level: str, message: str, **values: Any) -> None:
            stopped.set()
            thread.join()
            with self.db.engine.begin() as conn:
                conn.execute(
                    jobs.update()
                    .where(jobs.c.id == job_id)
                    .values(status=status, updated_at=now(), **values)
                )
                self.db.log(job_id, level, message, connection=conn)

        try:
            self.check(job_id)
            if job["kind"] == "sync":
                result = SyncService(
                    self.db, self.repository, self.provider, self.settings.sync_concurrency
                ).run(job, lambda: self.check(job_id))
                status = "partial" if result["warnings"] else "succeeded"
            elif job["kind"] in {"dca", "backtest"}:
                backtest = job["kind"] == "backtest"
                engine_version = BACKTEST_VERSION if backtest else DCA_VERSION
                version_key = "_backtest_version" if backtest else "_dca_version"
                if job["snapshot"].get(version_key) != engine_version or (
                    backtest and job["snapshot"].get("_strategy_version") != STRATEGY_VERSION
                ):
                    raise ProviderError(
                        "incompatible_engine_version", "研究引擎版本不兼容，请使用对应版本执行"
                    )
                request_dca = DcaRequest.model_validate(job["payload"]) if not backtest else None
                request_backtest = (
                    BacktestRequest.model_validate(job["payload"]) if backtest else None
                )
                pinned = {k: v for k, v in job["snapshot"].items() if not k.startswith("_")}
                if job["payload"]["data_versions"] != pinned:
                    raise ProviderError("invalid_version", "研究任务参数与固定快照不一致")
                if request_backtest is not None:
                    output = simulate_backtest(
                        request_backtest,
                        prepare_backtest(request_backtest, self.repository),
                        lambda: self.check(job_id),
                        lambda value: self.db.update(job_id, progress=value * 0.95),
                    )
                else:
                    assert request_dca is not None
                    output = simulate(
                        request_dca,
                        prepare(request_dca, self.repository),
                        lambda: self.check(job_id),
                        lambda value: self.db.update(job_id, progress=value * 0.95),
                    )
                output.update(
                    data_versions=pinned,
                    parameters=job["payload"],
                    fixture_mode=any(
                        self.repository.version(v, resolve_coverage=False)["source"] == "fixture"
                        for v in pinned.values()
                    ),
                )
                self.check(job_id)
                relative = f"results/{job_id}/{identifier()}.json"
                target = self.settings.data_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_suffix(".tmp")
                with temporary.open("w") as handle:
                    json.dump(output, handle, ensure_ascii=False, allow_nan=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                self.check(job_id)
                os.replace(temporary, target)
                result = {
                    "path": relative,
                    **output["summary"],
                    "warnings": output["warnings"],
                    "engine_version": engine_version,
                }
                status = "succeeded"
            elif job["kind"] == "screening":
                if (
                    job["snapshot"].get("_rule_version", RULE_VERSION) != RULE_VERSION
                    or job["snapshot"].get("_factor_version", FACTOR_VERSION) != FACTOR_VERSION
                ):
                    raise ProviderError(
                        "incompatible_engine_version",
                        "任务固定的计算版本与当前版本不同，请使用对应版本执行",
                    )
                job["snapshot"].setdefault("_universe_policy", "historical_v0")
                request = ScreeningRequest.model_validate(job["payload"])
                rows = ScreeningEngine(self.repository).run(
                    request,
                    job["snapshot"],
                    lambda: self.check(job_id),
                    lambda value: self.db.update(job_id, progress=value),
                )
                self.check(job_id)
                relative = f"results/{job_id}/{identifier()}.parquet"
                target = self.settings.data_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                # JSON columns preserve null/boolean/number semantics and explanations.
                flat = [
                    {
                        **{
                            k: v
                            for k, v in row.items()
                            if k
                            not in {
                                "factor_values",
                                "data_quality_flags",
                                "matched_factors",
                                "condition_results",
                            }
                        },
                        **{
                            k: json.dumps(row[k], ensure_ascii=False, allow_nan=False)
                            for k in [
                                "factor_values",
                                "data_quality_flags",
                                "matched_factors",
                                "condition_results",
                            ]
                        },
                    }
                    for row in rows
                ]
                frame = (
                    pl.DataFrame(flat)
                    if flat
                    else pl.DataFrame(
                        schema={
                            "stock_code": pl.String,
                            "stock_name": pl.String,
                            "as_of_date": pl.String,
                            "latest_trade_date": pl.String,
                            "match_status": pl.String,
                            "rule_id": pl.String,
                            "factor_values": pl.String,
                            "data_quality_flags": pl.String,
                            "matched_factors": pl.String,
                            "condition_results": pl.String,
                        }
                    )
                )
                temporary = target.with_suffix(".tmp")
                frame.write_parquet(temporary, compression="zstd")
                os.replace(temporary, target)
                result = {
                    "path": relative,
                    "total": len(rows),
                    "matched": sum(row["match_status"] == "matched" for row in rows),
                    "insufficient_data": sum(
                        row["match_status"] == "insufficient_data" for row in rows
                    ),
                    "rule_version": RULE_VERSION,
                    "factor_version": FACTOR_VERSION,
                }
                status = "succeeded"
            else:
                raise ProviderError("unknown_job_kind", "未知任务类型")
            self.check(job_id)
            finish(
                status,
                "info",
                "任务已完成" if status == "succeeded" else "同步完成，存在可选接口降级",
                progress=1,
                result=result,
                error=None,
            )
        except Cancelled:
            finish("cancelled", "warning", "任务已取消，已发布数据保留")
        except (ProviderError, ValueError) as exc:
            message = (
                str(exc) if isinstance(exc, ProviderError) else "任务数据或参数错误，请检查数据状态"
            )
            finish(
                "failed",
                "error",
                message,
                error={
                    "code": exc.code if isinstance(exc, ProviderError) else "invalid_data",
                    "message": message,
                }.__str__(),
            )
        except Exception as exc:
            # Never persist exception messages that may contain upstream secrets.
            finish(
                "failed",
                "error",
                f"内部错误类型：{type(exc).__name__}",
                error=f"内部错误 ({type(exc).__name__})，请运行诊断测试",
            )
        finally:
            stopped.set()
            thread.join(timeout=3)
        return True

    def serve(self, once: bool = False) -> None:
        lock_path: Path = self.settings.database_path.with_suffix(".worker.lock")
        with lock_path.open("w") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("同一数据库只允许一个 Worker") from exc
            self.recover()
            scheduler = None
            if not once:
                DailyDividendScheduler(self.settings, self.db, self.repository).tick()
                MonthlyNameScheduler(self.settings, self.db, self.repository).tick()
                MonthlyDividendScheduler(self.settings, self.db, self.repository).tick()
                DailyNameScheduler(self.settings, self.db, self.repository).tick()
                DailyIncrementalScheduler(self.settings, self.db, self.repository).tick()
                scheduler = create_scheduler(self.settings, self.db, self.repository)
                scheduler.start()
            try:
                while True:
                    ran = self.run_once()
                    if once:
                        return
                    if not ran:
                        time.sleep(self.settings.worker_poll_seconds)
            finally:
                if scheduler is not None:
                    scheduler.shutdown(wait=True)
