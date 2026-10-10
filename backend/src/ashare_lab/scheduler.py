"""Durable scheduled submissions, driven by the single locked Worker."""

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

from ashare_lab.config import Settings
from ashare_lab.db import Database, identifier, jobs, now, settings_table
from ashare_lab.warehouse import MarketRepository

TZ = ZoneInfo("Asia/Shanghai")
SYNC_POLICY = "history_incremental_v3"
SURVIVOR_SYNC_POLICIES = {"monthly_names_v1", "daily_dividends_v2", SYNC_POLICY}


def first_saturday(year: int, month: int, hour: int = 5) -> datetime:
    first = datetime(year, month, 1, hour, tzinfo=TZ)
    return first + timedelta(days=(5 - first.weekday()) % 7)


def monthly_due(instant: datetime, hour: int = 5) -> datetime:
    local = instant.astimezone(TZ)
    due = first_saturday(local.year, local.month, hour)
    if local < due:
        previous = local.replace(day=1) - timedelta(days=1)
        due = first_saturday(previous.year, previous.month, hour)
    return due


def daily_due(instant: datetime, hour: int = 1) -> datetime:
    local = instant.astimezone(TZ)
    due = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    return due if local >= due else due - timedelta(days=1)


class MonthlyNameScheduler:
    mode = "namechange"
    hour = 5
    history_refresh = "full"
    schedule = "每月第一个星期六 05:00"
    limitation = "每天回查30天发现变化，每月逐股全量校正窗口之外的修订；未知状态不视为非 ST。"

    def __init__(self, settings: Settings, db: Database, repository: MarketRepository):
        self.settings, self.db, self.repository = settings, db, repository

    def due(self, instant: datetime) -> datetime:
        return monthly_due(instant, self.hour)

    def slot(self, due: datetime) -> str:
        # Keep the original month key across schedule upgrades.
        return f"namechange:{due:%Y-%m}"

    def upcoming(self, local: datetime) -> datetime:
        upcoming = first_saturday(local.year, local.month, self.hour)
        if upcoming <= local:
            following = (local.replace(day=28) + timedelta(days=4)).replace(day=1)
            upcoming = first_saturday(following.year, following.month, self.hour)
        return upcoming

    def enabled(self) -> bool:
        return bool(self.settings.tushare_token.get_secret_value()) and not any(
            self.repository.version(v, resolve_coverage=False)["source"] == "fixture"
            for v in self.db.snapshot().values()
        )

    def tick(self, instant: datetime | None = None) -> None:
        instant = instant or datetime.now(TZ)
        if not self.enabled():
            return
        due = self.due(instant)
        # Existing complete history already covers this monthly slot: claim without replay.
        skip = False
        if self.mode in {"dividend", "namechange"} and self.history_refresh == "full":
            snapshot = self.db.snapshot()
            basics = self.repository.read("stock_basic", snapshot)
            codes = [
                r["stock_code"]
                for r in basics.to_dicts()
                if r.get("exchange") in {"SSE", "SZSE"}
                and r.get("list_status") != "D"
                and r["stock_code"].startswith(("0", "3", "6"))
            ]
            observation = self.repository.coverage(self.mode, snapshot).get(
                "history:observation", {}
            )
            full_day = observation.get("full_as_of_date", "")
            eligible = not observation or full_day >= (due.date() - timedelta(days=1)).isoformat()
            skip = (
                eligible
                and bool(codes)
                and set(codes)
                <= self.repository.history_complete_codes(
                    self.mode, (due.date() - timedelta(days=1)).isoformat(), snapshot
                )
            )
        job_id = identifier()
        with self.db.engine.begin() as conn:
            claimed = conn.execute(
                insert(settings_table)
                .values(
                    key=self.slot(due),
                    value={"job_id": None, "baseline_covered": True}
                    if skip
                    else {"job_id": job_id},
                )
                .on_conflict_do_nothing(index_elements=["key"])
            )
            if claimed.rowcount != 1:
                return
            if skip:
                return
            conn.execute(
                jobs.insert().values(
                    id=job_id,
                    kind="sync",
                    status="queued",
                    payload={
                        "mode": self.mode,
                        "end_date": (
                            due.date()
                            if self.mode == "incremental"
                            else due.date() - timedelta(days=1)
                        ).isoformat(),
                        **(
                            {"history_refresh": self.history_refresh}
                            if self.mode != "incremental"
                            else {}
                        ),
                    },
                    snapshot={**self.db.snapshot(conn), "_sync_policy": SYNC_POLICY},
                    progress=0,
                    cancel_requested=False,
                    created_at=now(),
                    updated_at=now(),
                    attempt=0,
                )
            )
        self.db.log(
            job_id, "info", f"{self.mode} 自动任务 {due.isoformat()}：北京时间 {self.schedule}"
        )

    def status(
        self,
        instant: datetime | None = None,
        *,
        snapshot: dict[str, str] | None = None,
        datasets: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        local = (instant or datetime.now(TZ)).astimezone(TZ)
        with self.db.engine.connect() as conn:
            query = (
                select(jobs)
                .where(jobs.c.kind == "sync", jobs.c.payload["mode"].as_string() == self.mode)
                .order_by(jobs.c.created_at.desc())
            )
            if self.mode in {"dividend", "namechange"}:
                strategy = jobs.c.payload["history_refresh"].as_string()
                query = query.where(strategy == self.history_refresh)
            latest = conn.execute(query.limit(1)).mappings().first()
            completed = (
                conn.execute(query.where(jobs.c.status == "succeeded").limit(1)).mappings().first()
            )
        if datasets is None:
            enabled = self.enabled()
            full_coverage = (
                self.repository.coverage(
                    self.mode, self.db.snapshot() if snapshot is None else snapshot
                )
                .get("history:observation", {})
                .get("full_as_of_date")
                if self.mode in {"dividend", "namechange"}
                else None
            )
        else:
            enabled = bool(self.settings.tushare_token.get_secret_value()) and not any(
                d["source"] == "fixture" for d in datasets
            )
            if self.mode == "incremental":
                enabled = enabled and any(
                    d["dataset"] == "daily" and d["partitions"] for d in datasets
                )
            full_coverage = next(
                (
                    d["coverage"].get("history:observation", {}).get("full_as_of_date")
                    for d in datasets
                    if d["dataset"] == self.mode
                ),
                None,
            )
        return {
            "enabled": bool(enabled),
            "timezone": "Asia/Shanghai",
            "schedule": self.schedule,
            "next_run": self.upcoming(local).isoformat(),
            "last_completed": completed["updated_at"] if completed else None,
            "latest_job": {"id": latest["id"], "status": latest["status"]} if latest else None,
            "limitation": self.limitation,
            "last_full_coverage": full_coverage,
        }


class DailyDividendScheduler(MonthlyNameScheduler):
    mode = "dividend"
    history_refresh = "incremental"
    hour = 1
    schedule = "每天 01:00"
    limitation = "已有分红继续使用；仅确认公开且已实施、派息的现金事件，未确认事件保持未知。"

    def due(self, instant: datetime) -> datetime:
        return daily_due(instant, hour=self.hour)

    def slot(self, due: datetime) -> str:
        return f"{self.mode}:{due:%Y-%m-%d}"

    def upcoming(self, local: datetime) -> datetime:
        return self.due(local) + timedelta(days=1)


class DailyNameScheduler(DailyDividendScheduler):
    mode = "namechange"
    hour = 2
    schedule = "每天 02:00"
    limitation = "回查30天公告及基础名称差异；每月全量校正补录、撤销和旧记录修订。"

    def slot(self, due: datetime) -> str:
        return f"namechange:daily:{due:%Y-%m-%d}"


class MonthlyDividendScheduler(MonthlyNameScheduler):
    mode = "dividend"
    hour = 3
    schedule = "每月第一个星期六 03:00"

    def slot(self, due: datetime) -> str:
        return f"dividend:full:{due:%Y-%m}"


class DailyIncrementalScheduler(DailyDividendScheduler):
    mode = "incremental"
    hour = 16
    schedule = "每天 16:00"
    limitation = "增量同步不包含分红与历史名称；需要先初始化真实行情。非交易日回查已有行情，数据未就绪时任务显示失败或部分完成。"

    def enabled(self) -> bool:
        if not super().enabled():
            return False
        version = self.db.snapshot().get("daily")
        # Check initialization without scanning every historical Parquet file each minute.
        return bool(
            version and self.repository.version(version, resolve_coverage=False)["manifest"]
        )


def create_scheduler(
    settings: Settings, db: Database, repository: MarketRepository
) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(
        timezone=TZ, job_defaults={"coalesce": True, "max_instances": 1}
    )
    for cls, job_id in [
        (MonthlyNameScheduler, "monthly_names"),
        (MonthlyDividendScheduler, "monthly_dividends"),
        (DailyNameScheduler, "daily_names"),
        (DailyDividendScheduler, "daily_dividends"),
        (DailyIncrementalScheduler, "daily_incremental"),
    ]:
        scheduler.add_job(cls(settings, db, repository).tick, "interval", minutes=1, id=job_id)
    return scheduler
