"""Hand-checkable regressions for bounded research and immutable statistics."""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import polars as pl
import pytest
from ashare_lab.config import Settings
from ashare_lab.db import versions
from ashare_lab.factors import FactorEngine
from ashare_lab.fixtures import FIXTURE_DATE
from ashare_lab.schemas import ScreeningRequest
from ashare_lab.screening import ScreeningEngine
from ashare_lab.warehouse import MarketRepository
from ashare_lab.worker import Worker
from sqlalchemy import delete, select


def test_version_summary_exact_duplicate_revision_and_empty(repository):
    frame = pl.DataFrame(
        {
            "stock_code": ["000001.SZ", "000001.SZ", "600000.SH"],
            "trade_date": ["2024-01-02", "2024-02-01", "2024-02-01"],
            "close": [10.0, 11.0, 20.0],
        }
    )
    # Fixture has other days: use a clean dataset with the same price partition layout.
    first = repository.publish("prices", frame, "range", "fixture", "one")
    duplicate = repository.publish("prices", frame, "range", "fixture", "two")
    revised = repository.publish(
        "prices",
        frame.head(1).with_columns(pl.lit(12.0).alias("close")),
        "revision",
        "fixture",
        "three",
    )
    for version in (first, duplicate, revised):
        assert repository.summary(version) == {
            "rows": 3,
            "start_date": "2024-01-02",
            "end_date": "2024-02-01",
            "stock_count": 2,
        }
    assert repository.read("prices", {"prices": first}).sort(["stock_code", "trade_date"])[
        "close"
    ].to_list() == [10.0, 11.0, 20.0]
    empty = repository.publish("empty", pl.DataFrame(), "empty", "fixture", "one")
    assert repository.summary(empty) == {
        "rows": 0,
        "start_date": None,
        "end_date": None,
        "stock_count": 0,
    }


def test_cold_summary_concurrency_restart_and_failure(repository):
    from ashare_lab import statistics
    from ashare_lab.db import dataset_summaries, partition_statistics

    version = repository.db.snapshot()["daily"]
    paths = repository.version(version, resolve_coverage=False)["manifest"]
    with repository.db.engine.begin() as conn:
        conn.execute(delete(dataset_summaries))
        conn.execute(delete(partition_statistics))
    original = statistics.inspect_file
    with patch.object(statistics, "inspect_file", wraps=original) as inspect:
        with ThreadPoolExecutor(max_workers=4) as pool:
            values = list(pool.map(lambda _: repository.summary(version), range(4)))
        assert values.count(values[0]) == 4
        assert inspect.call_count == len(paths)
        # Another repository/process cache must reuse durable summaries.
        assert MarketRepository(repository.db, repository.root).summary(version) == values[0]
        assert inspect.call_count == len(paths)
    with repository.db.engine.begin() as conn:
        conn.execute(delete(dataset_summaries))
        conn.execute(delete(partition_statistics))
    with patch.object(statistics, "inspect_file", side_effect=OSError("interruption")):
        with pytest.raises(OSError):
            repository.summary(version)
    with repository.db.engine.connect() as conn:
        assert conn.execute(select(dataset_summaries)).first() is None
    assert repository.summary(version) == values[0]


def test_month_bucket_pruning_long_suspension_and_legacy(repository):
    frame = pl.DataFrame(
        {
            "stock_code": ["000001.SZ", "600000.SH", "000001.SZ"],
            "trade_date": ["2020-01-02", "2024-01-02", "2025-01-02"],
            "close": [10.0, 20.0, 999.0],
        }
    )
    version = repository.publish("prices", frame, "range", "fixture", "one")
    rows = repository.read("prices", {"prices": version}, ["000001.SZ"], "2020-01-01", "2024-01-03")
    assert rows["close"].to_list() == [10.0]
    # Independent daily catalog with a stock suspended for four years.
    with repository.db.engine.begin() as conn:
        conn.execute(versions.update().where(versions.c.id == version).values(dataset="daily"))
    snapshot = {"daily": version}
    assert repository.last_trade_dates(
        snapshot, ["000001.SZ", "600000.SH", "000003.SZ"], "2024-01-03"
    ) == {"000001.SZ": "2020-01-02", "600000.SH": "2024-01-02"}
    manifest = repository.version(version, resolve_coverage=False)["manifest"]
    with repository.db.engine.begin() as conn:
        conn.execute(
            versions.update()
            .where(versions.c.id == version)
            .values(manifest={f"legacy-{i}": p for i, p in enumerate(manifest.values())})
        )
    assert repository.last_trade_dates(snapshot, ["000001.SZ"], "2024-01-03") == {
        "000001.SZ": "2020-01-02"
    }
    assert repository.read("daily", snapshot, ["000001.SZ"], end_date="2024-01-03")[
        "close"
    ].to_list() == [10.0]


def test_screening_batches_match_reference_progress_and_cancel(repository):
    snapshot = repository.db.snapshot()
    request = ScreeningRequest(as_of_date=FIXTURE_DATE, rule_id="A")
    reference = ScreeningEngine(repository).run(request, snapshot)
    progress = []
    with patch("ashare_lab.screening.SCREENING_BATCH_SIZE", 1):
        actual = ScreeningEngine(repository).run(request, snapshot, progress=progress.append)
    assert actual == reference
    assert progress == sorted(progress) and progress[-1] == pytest.approx(0.95)
    assert len(progress) >= 3

    def cancelled():
        raise RuntimeError("cancelled before any market calculation")

    with patch.object(FactorEngine, "compute", side_effect=AssertionError("must not compute")):
        with pytest.raises(RuntimeError, match="cancelled"):
            ScreeningEngine(repository).run(request, snapshot, cancelled)


def test_three_interrupted_research_runs_stop_but_sync_recovers(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, database_path=tmp_path / "business.db")
    worker = Worker(settings)
    original_snapshot = {"daily": "pinned-old-version"}
    research = worker.db.enqueue("screening", {}, original_snapshot)
    sync = worker.db.enqueue("sync", {}, original_snapshot)
    # Legacy attempts do not imply that the new guard has observed an interruption.
    worker.db.update(research["id"], attempt=44)
    for count in range(1, 4):
        for job in (research, sync):
            worker.db.update(job["id"], status="running")
        worker.recover()
        value = worker.db.job(research["id"])
        assert value["status"] == ("failed" if count == 3 else "queued")
        assert value["snapshot"]["daily"] == original_snapshot["daily"]
        assert value["snapshot"]["_recovery_interruptions"] == count
        assert worker.db.job(sync["id"])["status"] == "queued"
    assert "反复中断" in value["error"]
    worker.db.update(research["id"], status="cancel_requested", cancel_requested=True)
    worker.recover()
    assert worker.db.job(research["id"])["status"] == "cancelled"


def test_file_batches_schema_union_and_future_pruning(repository):
    from contextlib import contextmanager

    from ashare_lab import warehouse

    manifest = {}
    # 65 legacy files force two queries; an evolved column appears only in the last file.
    for i in range(65):
        relative = f"legacy-{i}.parquet"
        value = {"stock_code": ["000001.SZ"], "trade_date": ["2024-01-02"], "close": [float(i)]}
        if i == 64:
            value["new_field"] = ["present"]
        pl.DataFrame(value).write_parquet(repository.root / relative)
        manifest[f"legacy-{i}"] = relative
    version = repository.publish("batch_test", pl.DataFrame(), "empty", "fixture", "one")
    with repository.db.engine.begin() as conn:
        conn.execute(versions.update().where(versions.c.id == version).values(manifest=manifest))
    original = warehouse.market_connection
    sizes = []

    class Observed:
        def __init__(self, conn):
            self.conn = conn

        def __getattr__(self, name):
            return getattr(self.conn, name)

        def from_parquet(self, paths, **kwargs):
            sizes.append(len(paths))
            return self.conn.from_parquet(paths, **kwargs)

    @contextmanager
    def observed():
        with original() as conn:
            yield Observed(conn)

    with patch.object(warehouse, "market_connection", observed):
        frame = repository.read("batch_test", {"batch_test": version}, end_date="2024-01-02")
    assert frame.height == 65 and frame["close"].sum() == sum(range(65))
    assert frame["new_field"].drop_nulls().to_list() == ["present"]
    assert sizes == [64, 1]
    # A future partition need not even exist: it must never be opened.
    manifest["2025-01/07"] = "must-not-read.parquet"
    with repository.db.engine.begin() as conn:
        conn.execute(versions.update().where(versions.c.id == version).values(manifest=manifest))
    assert (
        repository.read("batch_test", {"batch_test": version}, end_date="2024-01-02").height == 65
    )


def test_publish_failure_rolls_back_statistics_and_head(repository):
    from ashare_lab.db import partition_statistics

    old = repository.db.snapshot()
    old_frame = repository.read("daily", old)
    with repository.db.engine.connect() as conn:
        old_paths = set(conn.execute(select(partition_statistics.c.path)).scalars())
    with patch("ashare_lab.warehouse.os.replace", side_effect=OSError("interrupted rename")):
        with pytest.raises(OSError):
            repository.publish(
                "daily",
                old_frame.with_columns(pl.lit(999.0).alias("close")),
                "revision",
                "fixture",
                "interrupted",
            )
    assert repository.db.snapshot() == old
    with repository.db.engine.connect() as conn:
        assert set(conn.execute(select(partition_statistics.c.path)).scalars()) == old_paths
    assert repository.read("daily", old).equals(old_frame)


def test_statistics_cli_does_not_overwrite_runtime_configuration(repository, monkeypatch, capsys):
    import sys

    from ashare_lab.cli import main
    from ashare_lab.db import settings_table

    with repository.db.engine.begin() as conn:
        conn.execute(
            settings_table.insert().values(key="runtime_config", value={"requests_per_minute": 200})
        )
    monkeypatch.chdir(repository.root)
    monkeypatch.setenv("DATA_DIR", str(repository.root))
    monkeypatch.setenv("DATABASE_PATH", str(repository.db.engine.url.database))
    monkeypatch.setattr(sys, "argv", ["ashare", "statistics"])
    main()
    assert "daily" in capsys.readouterr().out
    with repository.db.engine.connect() as conn:
        assert conn.execute(
            select(settings_table.c.value).where(settings_table.c.key == "runtime_config")
        ).scalar_one() == {"requests_per_minute": 200}


def test_market_cache_keeps_jobs_fresh_and_invalidates_on_new_versions(repository):
    from ashare_lab.services import ApplicationService

    settings = Settings(
        _env_file=None, data_dir=repository.root, database_path=repository.root / "business.db"
    )
    service = ApplicationService(settings)
    with patch.object(service.repository, "read", wraps=service.repository.read) as read:
        first = service.market_status(management=False)
        count = read.call_count
        job = service.db.enqueue("screening", {"as_of_date": FIXTURE_DATE})
        sync = service.db.enqueue("sync", {"mode": "incremental"})
        second = service.market_status(management=False)
        assert read.call_count == count
        assert second["recent_screening"][0]["id"] == job["id"]
        assert second["recent_sync"] == [] and second["latest_sync_status"] == "queued"
        assert service.market_status(management=True)["recent_sync"][0]["id"] == sync["id"]
        # A caller cannot poison the cached immutable facts by mutating its response.
        second["datasets"].clear()
        assert service.market_status()["datasets"] == first["datasets"]
        basics = service.repository.read("stock_basic", service.db.snapshot())
        changed = basics.with_columns(pl.lit("D").alias("list_status"))
        service.repository.publish("stock_basic", changed, "revision", "fixture", "new")
        current = service.market_status()
        assert current["stock_count"] == 0 and first["stock_count"] > 0


def test_encoded_market_response_matches_dict_and_keeps_permissions(repository):
    import json

    from ashare_lab.services import ApplicationService

    service = ApplicationService(
        Settings(
            _env_file=None, data_dir=repository.root, database_path=repository.root / "business.db"
        )
    )
    service.db.enqueue("sync", {"mode": "incremental"})
    for management in (False, True):
        assert json.loads(service.market_status_json(management)) == service.market_status(
            management
        )
    service.db.enqueue("screening", {"as_of_date": FIXTURE_DATE})
    encoded = json.loads(service.market_status_json(False))
    assert encoded["recent_screening"][0]["status"] == "queued" and encoded["recent_sync"] == []
