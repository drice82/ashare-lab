import fcntl
import json
import os
import signal
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

import polars as pl
from sqlalchemy import select, text

from ashare_lab.backtest import BACKTEST_VERSION, STRATEGY_VERSION
from ashare_lab.backtest import prepare as prepare_backtest
from ashare_lab.backtest import simulate as simulate_backtest
from ashare_lab.comparison import COMPARISON_VERSION, prepare_comparison, simulate_comparison
from ashare_lab.config import Settings
from ashare_lab.db import Database, identifier, jobs, now
from ashare_lab.dca import DCA_VERSION, prepare, simulate
from ashare_lab.factors import FACTOR_VERSION
from ashare_lab.notifications import NotificationService
from ashare_lab.portfolio import PORTFOLIO_VERSION, prepare_portfolio, simulate_portfolio
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
from ashare_lab.schemas import (
    BacktestCompareRequest,
    BacktestRequest,
    DcaRequest,
    PortfolioRequest,
    ScreeningRequest,
)
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
        self._stopping = threading.Event()
        self.notifications = NotificationService(settings, self.db, self.repository)

    def check(self, job_id: str) -> None:
        if self.db.is_cancelled(job_id):
            raise Cancelled()

    def recover(self) -> None:
        self.notifications.recover_deliveries()
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
                elif job["kind"] in {
                    "screening",
                    "dca",
                    "backtest",
                    "portfolio",
                    "notification_signal",
                }:
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

    def claim_job(self, sync: bool | None = None) -> dict[str, Any] | None:
        """Claim one queued task, optionally restricting it to an execution lane."""
        with self.db.engine.begin() as conn:
            conn.execute(text("BEGIN IMMEDIATE"))
            statement = select(jobs).where(jobs.c.status == "queued")
            if sync is not None:
                statement = statement.where(
                    jobs.c.kind == "sync" if sync else jobs.c.kind != "sync"
                )
            row = (
                conn.execute(statement.order_by(jobs.c.created_at, jobs.c.id).limit(1))
                .mappings()
                .first()
            )
            if row is None:
                return None
            job = dict(row)
            conn.execute(
                jobs.update()
                .where(jobs.c.id == job["id"], jobs.c.status == "queued")
                .values(
                    status="running", heartbeat=now(), updated_at=now(), attempt=job["attempt"] + 1
                )
            )
        return job

    def run_once(self) -> bool:
        job = self.claim_job()
        if job is None:
            return False
        self.execute_job(job)
        return True

    def execute_job(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        stopped = threading.Event()

        def heartbeat() -> None:
            while not stopped.wait(2):
                self.db.update(job_id, heartbeat=now())

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        completion_prepared = False

        def before_complete() -> None:
            # History publication commits success and its final log atomically.
            # Stop all task writes first: the API may delete as soon as it commits.
            nonlocal completion_prepared
            stopped.set()
            thread.join()
            completion_prepared = True

        def finish(status: str, level: str, message: str, **values: Any) -> None:
            stopped.set()
            thread.join()
            with self.db.engine.begin() as conn:
                conn.execute(text("BEGIN IMMEDIATE"))
                if (
                    status in {"succeeded", "partial"}
                    and conn.execute(
                        select(jobs.c.cancel_requested).where(jobs.c.id == job_id)
                    ).scalar_one()
                ):
                    status, level, message = ("cancelled", "warning", "任务已取消，已发布数据保留")
                    values = {}
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
                ).run(job, lambda: self.check(job_id), before_complete=before_complete)
                if completion_prepared:
                    return
                status = "partial" if result["warnings"] else "succeeded"
            elif job["kind"] == "notification_signal":
                result = self.notifications.execute_signal(job)
                status = "partial" if result["status"] == "insufficient_data" else "succeeded"
            elif job["kind"] in {"dca", "backtest", "portfolio"}:
                portfolio = job["kind"] == "portfolio"
                backtest = job["kind"] == "backtest"
                comparison = backtest and "strategies" in job["payload"]
                engine_version = (
                    PORTFOLIO_VERSION
                    if portfolio
                    else BACKTEST_VERSION
                    if backtest
                    else DCA_VERSION
                )
                version_key = (
                    "_portfolio_version"
                    if portfolio
                    else "_backtest_version"
                    if backtest
                    else "_dca_version"
                )
                if job["snapshot"].get(version_key) != engine_version or (
                    backtest
                    and not comparison
                    and job["snapshot"].get("_strategy_version") != STRATEGY_VERSION
                ):
                    raise ProviderError(
                        "incompatible_engine_version", "研究引擎版本不兼容，请使用对应版本执行"
                    )
                request_dca = (
                    DcaRequest.model_validate(job["payload"])
                    if not backtest and not portfolio
                    else None
                )
                request_backtest = (
                    BacktestRequest.model_validate(job["payload"])
                    if backtest and not comparison
                    else None
                )
                pinned = {k: v for k, v in job["snapshot"].items() if not k.startswith("_")}
                if job["payload"]["data_versions"] != pinned:
                    raise ProviderError("invalid_version", "研究任务参数与固定快照不一致")
                if portfolio:
                    request_portfolio = PortfolioRequest.model_validate(job["payload"])
                    strategy_versions = {
                        m.strategy.id: STRATEGY_VERSION
                        if m.strategy.id in {"buy_hold", "ma_cross"}
                        else COMPARISON_VERSION
                        for m in request_portfolio.members
                    }
                    if job["snapshot"].get("_backtest_version") != BACKTEST_VERSION or any(
                        job["snapshot"].get(f"_strategy_version_{key}") != value
                        for key, value in strategy_versions.items()
                    ):
                        raise ProviderError(
                            "incompatible_engine_version", "组合依赖的交易或策略版本不兼容"
                        )
                    output = simulate_portfolio(
                        request_portfolio,
                        prepare_portfolio(
                            request_portfolio, self.repository, lambda: self.check(job_id)
                        ),
                        lambda: self.check(job_id),
                        lambda value: self.db.update(job_id, progress=value * 0.95),
                    )
                    output["strategy_versions"] = strategy_versions
                elif comparison:
                    request_compare = BacktestCompareRequest.model_validate(job["payload"])
                    if job["snapshot"].get("_comparison_version") != COMPARISON_VERSION or any(
                        job["snapshot"].get(f"_strategy_version_{item.id}")
                        != (
                            STRATEGY_VERSION
                            if item.id in {"buy_hold", "ma_cross"}
                            else COMPARISON_VERSION
                        )
                        for item in request_compare.strategies
                    ):
                        raise ProviderError(
                            "incompatible_engine_version", "比较引擎或策略版本不兼容"
                        )
                    output = simulate_comparison(
                        request_compare,
                        prepare_comparison(request_compare, self.repository),
                        lambda: self.check(job_id),
                        lambda value: self.db.update(job_id, progress=value * 0.95),
                    )
                elif request_backtest is not None:
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

    def stop(self) -> None:
        """Stop dispatching; already submitted tasks drain before releasing the lock."""
        self._stopping.set()

    def _release_claim(self, job: dict[str, Any]) -> None:
        # Submission has not started execution. Preserve any concurrent cancellation
        # request; execute_job will honour it when the task is next claimed.
        with self.db.engine.begin() as conn:
            conn.execute(text("BEGIN IMMEDIATE"))
            row = conn.execute(select(jobs).where(jobs.c.id == job["id"])).mappings().first()
            if row is None or row["status"] not in {"running", "cancel_requested"}:
                return
            status = "cancelled" if row["cancel_requested"] else "queued"
            conn.execute(
                jobs.update()
                .where(jobs.c.id == job["id"])
                .values(status=status, heartbeat=None, attempt=job["attempt"], updated_at=now())
            )
            if status == "cancelled":
                self.db.log(job["id"], "warning", "任务执行前已取消", connection=conn)

    def run_loop(self) -> None:
        """Dispatch bounded lanes. Startup recovery and ownership belong to serve()."""
        research_limit = self.settings.worker_research_concurrency
        with (
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="sync-job") as sync_pool,
            ThreadPoolExecutor(
                max_workers=research_limit, thread_name_prefix="research-job"
            ) as research_pool,
        ):
            lanes = [(True, sync_pool, 1), (False, research_pool, research_limit)]
            senders: list[threading.Thread] = []
            senders_stopping = threading.Event()

            def send_loop(kind: str) -> None:
                while not self._stopping.is_set() and not senders_stopping.is_set():
                    try:
                        worked = self.notifications.send_once(kind)
                    except Exception:
                        # Raw upstream exceptions may contain credentials. Never log them.
                        worked = False
                    if not worked:
                        senders_stopping.wait(1)

            for kind in ("webhook", "email"):
                thread = threading.Thread(
                    target=send_loop, args=(kind,), name=f"notify-{kind}", daemon=True
                )
                thread.start()
                senders.append(thread)
            next_notification_tick = 0.0
            active: dict[bool, set[Future[None]]] = {True: set(), False: set()}
            try:
                while not self._stopping.is_set():
                    if time.monotonic() >= next_notification_tick:
                        self.notifications.schedule()
                        next_notification_tick = time.monotonic() + 60
                    for sync, pool, limit in lanes:
                        for future in tuple(active[sync]):
                            if future.done():
                                active[sync].remove(future)
                                future.result()
                        while len(active[sync]) < limit and not self._stopping.is_set():
                            job = self.claim_job(sync=sync)
                            if job is None:
                                break
                            if self._stopping.is_set():
                                self._release_claim(job)
                                break
                            try:
                                active[sync].add(pool.submit(self.execute_job, job))
                            except BaseException:
                                self._release_claim(job)
                                raise
                    self._stopping.wait(self.settings.worker_poll_seconds)
                # Surface executor errors even when shutdown happens before the next poll.
                for futures in active.values():
                    for future in futures:
                        future.result()

            finally:
                senders_stopping.set()
                for sender in senders:
                    sender.join(timeout=35)

    def serve(self, once: bool = False) -> None:
        lock_path: Path = self.settings.database_path.with_suffix(".worker.lock")
        with lock_path.open("w") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("同一数据库只允许一个 Worker") from exc
            self.recover()
            scheduler = None
            previous_signals: dict[signal.Signals, Any] = {}
            try:
                if once:
                    self.run_once()
                    return
                if threading.current_thread() is threading.main_thread():
                    for sig in (signal.SIGINT, signal.SIGTERM):
                        previous_signals[sig] = signal.getsignal(sig)
                        signal.signal(sig, lambda _sig, _frame: self.stop())
                DailyDividendScheduler(self.settings, self.db, self.repository).tick()
                MonthlyNameScheduler(self.settings, self.db, self.repository).tick()
                MonthlyDividendScheduler(self.settings, self.db, self.repository).tick()
                DailyNameScheduler(self.settings, self.db, self.repository).tick()
                DailyIncrementalScheduler(self.settings, self.db, self.repository).tick()
                scheduler = create_scheduler(self.settings, self.db, self.repository)
                scheduler.start()
                self.run_loop()
            finally:
                if scheduler is not None:
                    scheduler.shutdown(wait=True)
                for sig, previous in previous_signals.items():
                    signal.signal(sig, previous)
