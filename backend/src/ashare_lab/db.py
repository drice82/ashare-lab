import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    event,
    select,
)
from sqlalchemy.engine import Connection, Engine

metadata = MetaData()
jobs = Table(
    "jobs",
    metadata,
    Column("id", String, primary_key=True),
    Column("kind", String, nullable=False),
    Column("status", String, nullable=False),
    Column("payload", JSON, nullable=False),
    Column("snapshot", JSON, nullable=False),
    Column("progress", Float, default=0),
    Column("cancel_requested", Boolean, default=False),
    Column("heartbeat", String),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("error", Text),
    Column("result", JSON),
    Column("attempt", Integer, default=0),
)
logs = Table(
    "job_logs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("job_id", String, ForeignKey("jobs.id"), nullable=False),
    Column("at", String, nullable=False),
    Column("level", String, nullable=False),
    Column("message", Text, nullable=False),
)
checkpoints = Table(
    "sync_checkpoints",
    metadata,
    Column("job_id", String, ForeignKey("jobs.id"), primary_key=True),
    Column("key", String, primary_key=True),
    Column("version_id", String, ForeignKey("dataset_versions.id"), nullable=False),
)
versions = Table(
    "dataset_versions",
    metadata,
    Column("id", String, primary_key=True),
    Column("dataset", String, nullable=False),
    Column("manifest", JSON, nullable=False),
    Column("coverage", JSON, nullable=False),
    Column("created_at", String, nullable=False),
    Column("source", String, nullable=False),
    Column("batch_id", String, nullable=False),
)
heads = Table(
    "dataset_heads",
    metadata,
    Column("dataset", String, primary_key=True),
    Column("version_id", String, nullable=False),
)
coverage_entries = Table(
    "coverage_entries",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("dataset", String, nullable=False),
    Column("key", String, nullable=False),
    Column("version_id", String, ForeignKey("dataset_versions.id"), nullable=False),
    Column("value", JSON, nullable=False),
)
partition_statistics = Table(
    "partition_statistics",
    metadata,
    Column("path", String, primary_key=True),
    Column("format_version", Integer, primary_key=True),
    Column("value", JSON, nullable=False),
)
dataset_summaries = Table(
    "dataset_summaries",
    metadata,
    Column("version_id", String, ForeignKey("dataset_versions.id"), primary_key=True),
    Column("format_version", Integer, primary_key=True),
    Column("value", JSON, nullable=False),
    Column("created_at", String, nullable=False),
)
presets = Table(
    "presets",
    metadata,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("definition", JSON, nullable=False),
    Column("created_at", String, nullable=False),
)
rule_definitions = Table(
    "rule_definitions",
    metadata,
    Column("id", String, primary_key=True),
    Column("version", String, nullable=False),
    Column("definition", JSON, nullable=False),
)
settings_table = Table(
    "settings",
    metadata,
    Column("key", String, primary_key=True),
    Column("value", JSON, nullable=False),
)


def now() -> str:
    return datetime.now(UTC).isoformat()


def identifier() -> str:
    return str(uuid.uuid4())


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.engine: Engine = create_engine(f"sqlite:///{path}", connect_args={"timeout": 30})

        @event.listens_for(self.engine, "connect")
        def configure(connection: Any, _: Any) -> None:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=30000")
            connection.execute("PRAGMA synchronous=FULL")

    def initialize(self) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config()
        config.set_main_option(
            "script_location", str(Path(__file__).resolve().parent / "migrations")
        )
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

    def is_cancelled(self, job_id: str) -> bool:
        with self.engine.connect() as conn:
            return bool(
                conn.execute(
                    select(jobs.c.cancel_requested).where(jobs.c.id == job_id)
                ).scalar_one()
            )

    def snapshot(self, connection: Connection | None = None) -> dict[str, str]:
        if connection is not None:
            return dict(connection.execute(select(heads.c.dataset, heads.c.version_id)).all())
        with self.engine.connect() as conn:
            return self.snapshot(conn)

    def enqueue(
        self, kind: str, payload: dict[str, Any], snapshot: dict[str, str] | None = None
    ) -> dict[str, Any]:
        job_id = identifier()
        with self.engine.begin() as conn:
            conn.execute(
                jobs.insert().values(
                    id=job_id,
                    kind=kind,
                    status="queued",
                    payload=payload,
                    snapshot=snapshot if snapshot is not None else self.snapshot(conn),
                    progress=0,
                    cancel_requested=False,
                    created_at=now(),
                    updated_at=now(),
                    attempt=0,
                )
            )
        return self.job(job_id)

    def job(self, job_id: str) -> dict[str, Any]:
        with self.engine.connect() as conn:
            row = conn.execute(select(jobs).where(jobs.c.id == job_id)).mappings().first()
            if row is None:
                raise KeyError(job_id)
            result = dict(row)
            result["logs"] = [
                dict(x)
                for x in conn.execute(
                    select(logs).where(logs.c.job_id == job_id).order_by(logs.c.id)
                ).mappings()
            ]
            return result

    def update(self, job_id: str, **values: Any) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                jobs.update().where(jobs.c.id == job_id).values(updated_at=now(), **values)
            )

    def log(
        self, job_id: str, level: str, message: str, connection: Connection | None = None
    ) -> None:
        statement = logs.insert().values(job_id=job_id, at=now(), level=level, message=message)
        if connection is not None:
            connection.execute(statement)
        else:
            with self.engine.begin() as conn:
                conn.execute(statement)

    def completed(self, job_id: str, key: str) -> bool:
        with self.engine.connect() as conn:
            return (
                conn.execute(
                    select(checkpoints).where(
                        checkpoints.c.job_id == job_id, checkpoints.c.key == key
                    )
                ).first()
                is not None
            )

    def json_export(self) -> str:
        return json.dumps(self.snapshot(), sort_keys=True)
