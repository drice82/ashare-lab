import hashlib
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Any

import duckdb
import polars as pl
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert

from ashare_lab.db import (
    Database,
    checkpoints,
    coverage_entries,
    heads,
    identifier,
    jobs,
    now,
    partition_statistics,
    versions,
)

KEYS = {
    "stock_basic": ["stock_code"],
    "namechange": ["stock_code", "start_date"],
    "trade_cal": ["exchange", "cal_date"],
    "dividend": ["stock_code", "end_date", "ann_date", "imp_ann_date", "ex_date"],
    "suspend_d": ["stock_code", "trade_date", "suspend_type", "suspend_timing"],
}
QUERY_LOCK = RLock()
FILE_BATCH_SIZE = 64
PARTITION = re.compile(r"^(all|\d{4}-\d{2})/(0[0-9]|1[0-5])$")


def stock_bucket(code: str) -> str:
    return f"{int(hashlib.sha256(code.encode()).hexdigest()[:8], 16) % 16:02d}"


@contextmanager
def market_connection() -> Iterator[duckdb.DuckDBPyConnection]:
    with QUERY_LOCK, duckdb.connect(config={"threads": 2, "memory_limit": "256MB"}) as conn:
        yield conn


PROVENANCE = ["source", "batch_id", "schema_version", "fetched_at"]


class MarketRepository:
    def __init__(self, db: Database, root: Path):
        self.db = db
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def version(self, version_id: str, resolve_coverage: bool = True) -> dict[str, Any]:
        with self.db.engine.connect() as conn:
            row = (
                conn.execute(select(versions).where(versions.c.id == version_id)).mappings().first()
            )
            if row is None:
                raise KeyError(version_id)
            result = dict(row)
            if resolve_coverage and "watermark" in result["coverage"]:
                watermark = result["coverage"]["watermark"]
                latest = (
                    select(
                        coverage_entries.c.key, func.max(coverage_entries.c.id).label("latest_id")
                    )
                    .where(
                        coverage_entries.c.dataset == result["dataset"],
                        coverage_entries.c.id <= watermark,
                    )
                    .group_by(coverage_entries.c.key)
                    .subquery()
                )
                result["coverage"] = dict(
                    conn.execute(
                        select(coverage_entries.c.key, coverage_entries.c.value).join(
                            latest, coverage_entries.c.id == latest.c.latest_id
                        )
                    ).all()
                )
            return result

    def summary(self, version_id: str) -> dict[str, Any]:
        from ashare_lab.statistics import summarize

        manifest = self.version(version_id, resolve_coverage=False)["manifest"]
        return summarize(self.db, self.root, version_id, manifest)

    def latest_date(self, dataset: str, snapshot: dict[str, str]) -> str | None:
        if dataset not in snapshot:
            return None
        return self.summary(snapshot[dataset])["end_date"]

    def select_manifest(
        self,
        dataset: str,
        manifest: dict[str, str],
        codes: list[str] | None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, str]:
        buckets = {stock_bucket(code) for code in codes} if codes is not None else None
        selected = {}
        for key, path in manifest.items():
            match = PARTITION.fullmatch(key)
            # Unknown legacy layout must remain readable.
            if match is None:
                selected[key] = path
                continue
            month, bucket = match.groups()
            if dataset != "trade_cal" and buckets is not None and bucket not in buckets:
                continue
            if month != "all" and (
                (start_date is not None and month < start_date[:7])
                or (end_date is not None and month > end_date[:7])
            ):
                continue
            selected[key] = path
        return selected

    def _last_dates(self, paths: list[str], codes: list[str], as_of: str) -> dict[str, str]:
        output: dict[str, str] = {}
        for offset in range(0, len(paths), FILE_BATCH_SIZE):
            with market_connection() as conn:
                conn.register(
                    "market",
                    conn.from_parquet(paths[offset : offset + FILE_BATCH_SIZE], union_by_name=True),
                )
                placeholders = ",".join("?" for _ in codes)
                rows = conn.execute(
                    f"SELECT stock_code, max(trade_date) FROM market WHERE trade_date <= ? AND stock_code IN ({placeholders}) GROUP BY stock_code",
                    [as_of, *codes],
                ).fetchall()
                for code, day in rows:
                    if day is not None:
                        output[code] = max(output.get(code, ""), day)
        return output

    def last_trade_dates(
        self, snapshot: dict[str, str], codes: list[str], as_of: str
    ) -> dict[str, str]:
        if "daily" not in snapshot or not codes:
            return {}
        manifest = self.version(snapshot["daily"], resolve_coverage=False)["manifest"]
        selected = self.select_manifest("daily", manifest, codes, end_date=as_of)
        if any(PARTITION.fullmatch(k) is None or k.startswith("all/") for k in selected):
            return self._last_dates([str(self.root / p) for p in selected.values()], codes, as_of)
        output: dict[str, str] = {}
        pending = set(codes)
        for month in sorted({k[:7] for k in selected}, reverse=True):
            if not pending:
                break
            paths = self.select_manifest(
                "daily", {k: p for k, p in selected.items() if k[:7] == month}, sorted(pending)
            )
            found = self._last_dates(
                [str(self.root / p) for p in paths.values()], sorted(pending), as_of
            )
            output.update(found)
            pending.difference_update(found)
        return output

    def read(
        self,
        dataset: str,
        snapshot: dict[str, str],
        codes: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pl.DataFrame:
        if dataset not in snapshot:
            return pl.DataFrame()
        manifest = self.version(snapshot[dataset], resolve_coverage=False)["manifest"]
        selected = self.select_manifest(dataset, manifest, codes, start_date, end_date)
        paths = [str(self.root / item) for item in selected.values()]
        if not paths:
            # Keep the schema when pruning removes every file (e.g. a suspended stock).
            return (
                pl.DataFrame(
                    schema=pl.read_parquet_schema(self.root / next(iter(manifest.values())))
                )
                if manifest
                else pl.DataFrame()
            )
        frames = []
        for offset in range(0, len(paths), FILE_BATCH_SIZE):
            with market_connection() as conn:
                relation = conn.from_parquet(
                    paths[offset : offset + FILE_BATCH_SIZE], union_by_name=True
                )
                columns = relation.columns
                filters: list[str] = []
                params: list[Any] = []
                if codes is not None and "stock_code" in columns:
                    if not codes:
                        return pl.DataFrame(schema=pl.read_parquet_schema(paths[0]))
                    filters.append("stock_code IN (" + ",".join("?" for _ in codes) + ")")
                    params.extend(codes)
                date_col = (
                    "trade_date"
                    if "trade_date" in columns
                    else "cal_date"
                    if "cal_date" in columns
                    else None
                )
                if date_col and start_date:
                    filters.append(f"{date_col} >= ?")
                    params.append(start_date)
                if date_col and end_date:
                    filters.append(f"{date_col} <= ?")
                    params.append(end_date)
                conn.register("market", relation)
                sql = "SELECT * FROM market" + (
                    " WHERE " + " AND ".join(filters) if filters else ""
                )
                result = pl.from_arrow(conn.execute(sql, params).to_arrow_table())
                assert isinstance(result, pl.DataFrame)
                frames.append(result)
        return pl.concat(frames, how="diagonal_relaxed")

    def publish(
        self,
        dataset: str,
        frame: pl.DataFrame,
        coverage_key: str,
        source: str,
        batch_id: str,
        checkpoint: tuple[str, str] | None = None,
        coverage_info: dict[str, Any] | None = None,
        replace_codes: list[str] | None = None,
        complete_job: tuple[str, dict[str, Any]] | None = None,
    ) -> str:
        snapshot = self.db.snapshot()
        previous = (
            self.version(snapshot[dataset], resolve_coverage=False) if dataset in snapshot else None
        )
        manifest: dict[str, str] = dict(previous["manifest"]) if previous else {}
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in frame.to_dicts():
            date = row.get("trade_date") or row.get("cal_date") or "all"
            month = str(date)[:7] if date != "all" else "all"
            bucket = (
                int(
                    hashlib.sha256(str(row.get("stock_code", "calendar")).encode()).hexdigest()[:8],
                    16,
                )
                % 16
            )
            partition = f"{month}/{bucket:02d}"
            groups.setdefault(partition, []).append(row)
        if replace_codes:
            if dataset not in {"dividend", "namechange"}:
                raise ValueError("只能替换完整名称或分红历史")
            if frame.height and frame.filter(~pl.col("stock_code").is_in(replace_codes)).height:
                raise ValueError("替换记录超出目标股票")
            old_rows = self.read(dataset, snapshot, replace_codes)
            returned_codes = set(frame["stock_code"].to_list()) if frame.height else set()
            if old_rows.height and set(old_rows["stock_code"].to_list()) - returned_codes:
                from ashare_lab.providers import ProviderError

                raise ProviderError("empty_history", "已有历史此次返回空结果，保留原记录")
            buckets = {
                f"{int(hashlib.sha256(code.encode()).hexdigest()[:8], 16) % 16:02d}"
                for code in replace_codes
            }
            for partition in manifest:
                if partition.split("/")[-1] not in buckets:
                    continue
                if (
                    pl.read_parquet(self.root / manifest[partition], columns=["stock_code"])
                    .filter(pl.col("stock_code").is_in(replace_codes))
                    .height
                ):
                    groups.setdefault(partition, [])
        file_statistics: dict[str, dict[str, Any]] = {}
        for partition, rows in groups.items():
            incoming = pl.DataFrame(rows, schema=frame.schema)
            old = (
                pl.read_parquet(self.root / manifest[partition])
                if partition in manifest
                else incoming.clear()
            )
            retained = (
                old.filter(~pl.col("stock_code").is_in(replace_codes)) if replace_codes else old
            )
            merged = pl.concat([retained, incoming], how="diagonal_relaxed").unique(
                subset=KEYS.get(dataset, ["stock_code", "trade_date"]),
                keep="last",
                maintain_order=True,
            )
            sort_keys = KEYS.get(dataset, ["stock_code", "trade_date"])
            merged = merged.sort(sort_keys)
            # Ignore ingestion metadata when determining whether market contents changed.
            data_cols = [c for c in merged.columns if c not in PROVENANCE]
            comparable_old = old.with_columns(
                pl.lit(None, dtype=merged.schema[column]).alias(column)
                for column in data_cols
                if column not in old.columns
            )
            if old.height == merged.height and comparable_old.sort(sort_keys).select(
                data_cols
            ).equals(merged.select(data_cols)):
                continue
            relative = f"market/{dataset}/{partition}/{identifier()}.parquet"
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".tmp")
            merged.write_parquet(temporary, compression="zstd", statistics=True)
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            manifest[partition] = relative
            from ashare_lab.statistics import frame_statistics

            file_statistics[relative] = frame_statistics(merged)
        entry = {"complete": True, "rows": frame.height, **(coverage_info or {})}
        version_id = identifier()
        with self.db.engine.begin() as conn:
            from ashare_lab.statistics import FORMAT_VERSION

            for path, value in file_statistics.items():
                conn.execute(
                    insert(partition_statistics)
                    .values(path=path, format_version=FORMAT_VERSION, value=value)
                    .on_conflict_do_nothing()
                )
            if complete_job:
                cancelled: bool = conn.execute(
                    select(jobs.c.cancel_requested).where(jobs.c.id == complete_job[0])
                ).scalar_one()
                if cancelled:
                    from ashare_lab.worker import Cancelled

                    raise Cancelled()
            conn.execute(
                versions.insert().values(
                    id=version_id,
                    dataset=dataset,
                    manifest=manifest,
                    coverage={},
                    created_at=now(),
                    source=source,
                    batch_id=batch_id,
                )
            )
            # Seed old-format heads once; old versions retain their original full snapshots.
            if previous and "watermark" not in previous["coverage"]:
                for old_key, value in previous["coverage"].items():
                    conn.execute(
                        coverage_entries.insert().values(
                            dataset=dataset, key=old_key, version_id=previous["id"], value=value
                        )
                    )
            inserted = conn.execute(
                coverage_entries.insert().values(
                    dataset=dataset, key=coverage_key, version_id=version_id, value=entry
                )
            )
            assert inserted.inserted_primary_key is not None
            watermark = inserted.inserted_primary_key[0]
            conn.execute(
                versions.update()
                .where(versions.c.id == version_id)
                .values(coverage={"watermark": watermark})
            )
            conn.execute(
                insert(heads)
                .values(dataset=dataset, version_id=version_id)
                .on_conflict_do_update(index_elements=["dataset"], set_={"version_id": version_id})
            )
            if checkpoint:
                conn.execute(
                    insert(checkpoints)
                    .values(job_id=checkpoint[0], key=checkpoint[1], version_id=version_id)
                    .on_conflict_do_nothing()
                )
            if complete_job:
                result = {**complete_job[1], "datasets": {**snapshot, dataset: version_id}}
                conn.execute(
                    jobs.update()
                    .where(jobs.c.id == complete_job[0])
                    .values(
                        status="succeeded", progress=1, result=result, error=None, updated_at=now()
                    )
                )
        return version_id

    def coverage(self, dataset: str, snapshot: dict[str, str]) -> dict[str, Any]:
        return self.version(snapshot[dataset])["coverage"] if dataset in snapshot else {}

    def covered_date(self, dataset: str, day: str, snapshot: dict[str, str]) -> bool:
        coverage = self.coverage(dataset, snapshot)
        return any(
            v.get("complete") and (v.get("trade_date") == day or day in v.get("trade_dates", []))
            for v in coverage.values()
        )

    def covered_stock(self, dataset: str, code: str, snapshot: dict[str, str]) -> bool:
        coverage = self.coverage(dataset, snapshot)
        return bool(
            coverage.get(f"stock:{code}", {}).get("complete")
            or any(
                row.get("complete") and code in row.get("complete_codes", [])
                for row in coverage.values()
            )
        )

    def history_complete_codes(
        self, dataset: str, as_of: str, snapshot: dict[str, str]
    ) -> set[str]:
        coverage = self.coverage(dataset, snapshot)
        legacy_date = (
            self.version(snapshot[dataset], resolve_coverage=False)["created_at"][:10]
            if dataset in snapshot
            else ""
        )
        baselines: dict[str, str] = {}
        for key, row in coverage.items():
            if not row.get("complete") or key.startswith("history:"):
                continue
            codes = (
                [key.removeprefix("stock:")]
                if key.startswith("stock:")
                else row.get("complete_codes", [])
            )
            for code in codes:
                baselines[code] = max(baselines.get(code, ""), row.get("as_of_date", legacy_date))
        completed = {code for code, day in baselines.items() if day >= as_of}
        observation = coverage.get("history:observation", {})
        if observation.get("complete") and observation.get("as_of_date", "") >= as_of:
            completed.update(
                code for code in observation.get("complete_codes", []) if code in baselines
            )
        return completed
