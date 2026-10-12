"""Collection and aggregation of browser-level anonymous page views."""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from threading import Lock
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import UUID4
from sqlalchemy import delete, distinct, func, select
from sqlalchemy.dialects.sqlite import insert

from ashare_lab.analytics_models import pageviews
from ashare_lab.db import Database
from ashare_lab.schemas import StrictModel
from ashare_lab.services import ServiceError

INGEST_PATH = "/api/v1/analytics/pageviews"
RETENTION_DAYS = 90
PAGE_NAMES = {
    "screening": "股票筛选",
    "dca": "定投模拟",
    "backtest": "单股回测",
    "portfolio": "组合回测",
    "watchlists": "自选列表",
    "knowledge": "知识库",
    "notifications": "信号通知",
    "stock_detail": "股票详情",
}


class PageViewRequest(StrictModel):
    event_id: UUID4
    visitor_id: UUID4
    page: Literal[
        "screening",
        "dca",
        "backtest",
        "portfolio",
        "watchlists",
        "knowledge",
        "notifications",
        "stock_detail",
    ]


class AnalyticsService:
    def __init__(
        self,
        db: Database,
        timezone: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.db = db
        self.timezone = ZoneInfo(timezone)
        self.clock = clock or (lambda: datetime.now(UTC))
        self._pruned_day: date | None = None
        self._prune_lock = Lock()

    def _cleanup(self, today: date) -> None:
        with self._prune_lock:
            if self._pruned_day == today:
                return
            earliest = (today - timedelta(days=RETENTION_DAYS - 1)).isoformat()
            with self.db.engine.begin() as conn:
                conn.execute(delete(pageviews).where(pageviews.c.local_date < earliest))
            self._pruned_day = today

    def record(self, request: PageViewRequest) -> None:
        stamp = self.clock().astimezone(UTC)
        today = stamp.astimezone(self.timezone).date()
        self._cleanup(today)
        with self.db.engine.begin() as conn:
            conn.execute(
                insert(pageviews)
                .values(
                    event_id=str(request.event_id),
                    visitor_hash=sha256(str(request.visitor_id).encode()).hexdigest(),
                    page=request.page,
                    occurred_at=stamp.isoformat(),
                    local_date=today.isoformat(),
                )
                .on_conflict_do_nothing(index_elements=[pageviews.c.event_id])
            )

    def summary(self, start_date: date | None, end_date: date | None) -> dict[str, Any]:
        today = self.clock().astimezone(self.timezone).date()
        earliest = today - timedelta(days=RETENTION_DAYS - 1)
        end = end_date or today
        start = start_date or max(earliest, end - timedelta(days=29))
        if not earliest <= start <= end <= today:
            raise ServiceError(
                422, "invalid_parameters", "统计日期须在最近90天内，开始日期不能晚于结束日期"
            )
        self._cleanup(today)
        views = func.count().label("pageviews")
        visitors = func.count(distinct(pageviews.c.visitor_hash)).label("visitors")
        condition = pageviews.c.local_date.between(start.isoformat(), end.isoformat())
        with self.db.engine.connect() as conn:
            # SQLite otherwise starts no transaction for SELECT; all three aggregates
            # must share a snapshot even while other browsers submit new events.
            conn.exec_driver_sql("BEGIN")
            totals = dict(conn.execute(select(views, visitors).where(condition)).mappings().one())
            daily = {
                row["local_date"]: dict(row)
                for row in conn.execute(
                    select(pageviews.c.local_date, views, visitors)
                    .where(condition)
                    .group_by(pageviews.c.local_date)
                ).mappings()
            }
            pages = [
                {
                    **dict(row),
                    "name": PAGE_NAMES[row["page"]],
                    "share": row["pageviews"] / totals["pageviews"] if totals["pageviews"] else 0,
                }
                for row in conn.execute(
                    select(pageviews.c.page, views, visitors)
                    .where(condition)
                    .group_by(pageviews.c.page)
                    .order_by(views.desc(), pageviews.c.page)
                ).mappings()
            ]
        days = []
        for offset in range((end - start).days + 1):
            day = (start + timedelta(days=offset)).isoformat()
            row = daily.get(day, {})
            days.append(
                {
                    "date": day,
                    "pageviews": row.get("pageviews", 0),
                    "visitors": row.get("visitors", 0),
                }
            )
        return {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "today": today.isoformat(),
            "earliest_date": earliest.isoformat(),
            "timezone": str(self.timezone),
            "retention_days": RETENTION_DAYS,
            "totals": totals,
            "daily": days,
            "pages": pages,
        }
