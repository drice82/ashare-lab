"""Rebuildable, exact statistics keyed by immutable files and catalog versions."""

from pathlib import Path
from threading import RLock
from typing import Any

import polars as pl
import pyarrow.parquet as pq
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

from ashare_lab.db import Database, dataset_summaries, now, partition_statistics

FORMAT_VERSION = 1
# All repositories in a process share the lock, including simultaneous cold API requests.
SUMMARY_LOCK = RLock()


def frame_statistics(frame: pl.DataFrame) -> dict[str, Any]:
    column = next((c for c in ("trade_date", "cal_date", "pay_date") if c in frame.columns), None)
    return {
        "rows": frame.height,
        "start_date": frame[column].min() if column else None,
        "end_date": frame[column].max() if column else None,
        "stock_codes": sorted(frame["stock_code"].drop_nulls().unique().to_list())
        if "stock_code" in frame.columns
        else [],
    }


def inspect_file(path: Path) -> dict[str, Any]:
    # One file at a time: never bind the full historical manifest in DuckDB.
    with pq.ParquetFile(path) as file:
        columns = file.schema_arrow.names
        column = next((c for c in ("trade_date", "cal_date", "pay_date") if c in columns), None)
        minimum, maximum = None, None
        if column:
            index = file.schema_arrow.get_field_index(column)
            ranges = [
                file.metadata.row_group(i).column(index).statistics
                for i in range(file.metadata.num_row_groups)
            ]
            if all(s is not None and (s.num_values == 0 or s.has_min_max) for s in ranges):
                starts = [s.min for s in ranges if s is not None and s.has_min_max]
                ends = [s.max for s in ranges if s is not None and s.has_min_max]
                minimum, maximum = min(starts, default=None), max(ends, default=None)
            else:
                values = [
                    v for v in file.read(columns=[column])[column].to_pylist() if v is not None
                ]
                minimum, maximum = min(values, default=None), max(values, default=None)
        codes = (
            sorted(
                v
                for v in file.read(columns=["stock_code"])["stock_code"].unique().to_pylist()
                if v is not None
            )
            if "stock_code" in columns
            else []
        )
        return {
            "rows": file.metadata.num_rows,
            "start_date": minimum,
            "end_date": maximum,
            "stock_codes": codes,
        }


def summarize(
    db: Database, root: Path, version_id: str, manifest: dict[str, str]
) -> dict[str, Any]:
    with SUMMARY_LOCK:
        with db.engine.connect() as conn:
            cached = conn.execute(
                select(dataset_summaries.c.value).where(
                    dataset_summaries.c.version_id == version_id,
                    dataset_summaries.c.format_version == FORMAT_VERSION,
                )
            ).scalar_one_or_none()
            if cached is not None:
                return dict(cached)
        rows = 0
        starts: list[str] = []
        ends: list[str] = []
        codes: set[str] = set()
        paths = list(manifest.values())
        for offset in range(0, len(paths), 256):
            batch = paths[offset : offset + 256]
            with db.engine.connect() as conn:
                known: dict[str, dict[str, Any]] = dict(
                    conn.execute(
                        select(partition_statistics.c.path, partition_statistics.c.value).where(
                            partition_statistics.c.path.in_(batch),
                            partition_statistics.c.format_version == FORMAT_VERSION,
                        )
                    ).all()
                )
            pending: list[dict[str, Any]] = []
            for path in batch:
                value = known.get(path)
                if value is None:
                    value = inspect_file(root / path)
                    pending.append({"path": path, "format_version": FORMAT_VERSION, "value": value})
                    known[path] = value
                rows += value["rows"]
                if value["start_date"] is not None:
                    starts.append(value["start_date"])
                if value["end_date"] is not None:
                    ends.append(value["end_date"])
                codes.update(value["stock_codes"])
            if pending:
                # Commit bounded progress without one disk fsync per historical file.
                with db.engine.begin() as conn:
                    conn.execute(insert(partition_statistics).on_conflict_do_nothing(), pending)
        result = {
            "rows": rows,
            "start_date": min(starts, default=None),
            "end_date": max(ends, default=None),
            "stock_count": len(codes),
        }
        with db.engine.begin() as conn:
            conn.execute(
                insert(dataset_summaries)
                .values(
                    version_id=version_id,
                    format_version=FORMAT_VERSION,
                    value=result,
                    created_at=now(),
                )
                .on_conflict_do_nothing()
            )
        return result
