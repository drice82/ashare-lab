"""Account-owned stock lists, independent from immutable research requests."""

import re
from typing import Any

from pydantic import Field, field_validator
from sqlalchemy import (
    JSON,
    Column,
    ForeignKey,
    Integer,
    String,
    Table,
    UniqueConstraint,
    delete,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError

from ashare_lab.db import Database, identifier, metadata, now
from ashare_lab.schemas import StockCode, StrictModel
from ashare_lab.services import ServiceError

watchlists = Table(
    "watchlists",
    metadata,
    Column("id", String, primary_key=True),
    Column("owner_id", String, ForeignKey("notification_owners.id"), nullable=False),
    Column("name", String(80), nullable=False),
    Column("codes", JSON, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("owner_id", "name", name="uq_watchlists_owner_name"),
)


class WatchlistInput(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    codes: list[StockCode] = Field(default_factory=list, max_length=6000)

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("codes")
    @classmethod
    def a_share_codes(cls, value: list[str]) -> list[str]:
        for code in value:
            if not re.fullmatch(
                r"(?:(?:600|601|603|605|688)\d{3}\.SH|(?:000|001|002|003|300|301)\d{3}\.SZ)",
                code,
            ):
                raise ValueError("自选列表只支持沪深A股代码")
        return list(dict.fromkeys(value))


class WatchlistUpdate(WatchlistInput):
    revision: int = Field(ge=1, strict=True)


class WatchlistService:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def public(row: Any) -> dict[str, Any]:
        return {
            key: row[key] for key in ("id", "name", "codes", "revision", "created_at", "updated_at")
        }

    def list(self, owner: str) -> list[dict[str, Any]]:
        with self.db.engine.connect() as conn:
            return [
                self.public(row)
                for row in conn.execute(
                    select(watchlists)
                    .where(watchlists.c.owner_id == owner)
                    .order_by(watchlists.c.created_at, watchlists.c.id)
                ).mappings()
            ]

    def get(self, owner: str, list_id: str) -> dict[str, Any]:
        with self.db.engine.connect() as conn:
            row = (
                conn.execute(
                    select(watchlists).where(
                        watchlists.c.owner_id == owner, watchlists.c.id == list_id
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise ServiceError(404, "watchlist_not_found", "自选列表不存在")
        return self.public(row)

    def create(self, owner: str, payload: WatchlistInput) -> dict[str, Any]:
        stamp = now()
        row = {
            "id": identifier(),
            "owner_id": owner,
            **payload.model_dump(),
            "revision": 1,
            "created_at": stamp,
            "updated_at": stamp,
        }
        try:
            with self.db.engine.begin() as conn:
                conn.execute(watchlists.insert().values(**row))
        except IntegrityError as exc:
            raise ServiceError(409, "watchlist_name_exists", "已有同名列表，请修改名称") from exc
        return self.public(row)

    def change(self, owner: str, list_id: str, payload: WatchlistUpdate) -> dict[str, Any]:
        values = {
            **payload.model_dump(exclude={"revision"}),
            "revision": payload.revision + 1,
            "updated_at": now(),
        }
        try:
            with self.db.engine.begin() as conn:
                row = (
                    conn.execute(
                        update(watchlists)
                        .where(
                            watchlists.c.owner_id == owner,
                            watchlists.c.id == list_id,
                            watchlists.c.revision == payload.revision,
                        )
                        .values(**values)
                        .returning(watchlists)
                    )
                    .mappings()
                    .first()
                )
                if row is None:
                    self._missing_or_stale(conn, owner, list_id)
                return self.public(row)
        except IntegrityError as exc:
            raise ServiceError(409, "watchlist_name_exists", "已有同名列表，请修改名称") from exc

    def remove(self, owner: str, list_id: str, revision: int) -> dict[str, str]:
        with self.db.engine.begin() as conn:
            removed = conn.execute(
                delete(watchlists)
                .where(
                    watchlists.c.owner_id == owner,
                    watchlists.c.id == list_id,
                    watchlists.c.revision == revision,
                )
                .returning(watchlists.c.id)
            ).first()
            if removed is None:
                self._missing_or_stale(conn, owner, list_id)
        return {"status": "deleted"}

    @staticmethod
    def _missing_or_stale(conn: Any, owner: str, list_id: str) -> None:
        exists = conn.execute(
            select(watchlists.c.id).where(
                watchlists.c.owner_id == owner, watchlists.c.id == list_id
            )
        ).first()
        if exists is None:
            raise ServiceError(404, "watchlist_not_found", "自选列表不存在")
        raise ServiceError(
            409, "watchlist_revision_conflict", "列表已在其他页面或设备修改，请刷新列表后重试"
        )
