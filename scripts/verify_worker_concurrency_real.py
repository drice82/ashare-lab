"""Real mixed Worker load: read-only source, copied catalog, isolated publications.

Run in a 2GiB container with the production volume mounted read-only at --data-dir.
Live requests are limited to one stock's basics and dividends; credentials come
only from environment variables. No scheduler or production tasks are executed.
"""

import argparse
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import polars as pl
from ashare_lab.config import Settings
from ashare_lab.db import jobs
from ashare_lab.providers import ProviderError, TushareProvider
from ashare_lab.scheduler import SYNC_POLICY
from ashare_lab.schemas import (
    BacktestCompareRequest,
    DcaRequest,
    ScreeningRequest,
    StrategyConfig,
    SyncRequest,
)
from ashare_lab.services import ApplicationService
from ashare_lab.worker import Worker
from sqlalchemy import select

CODE = "000001.SZ"


class BoundedProvider(TushareProvider):
    def fetch(self, dataset: str, params: dict[str, str]) -> pl.DataFrame:
        assert dataset in {"stock_basic", "dividend"}
        assert "ts_code" not in params or params["ts_code"] == CODE
        return super().fetch(dataset, {**params, "ts_code": CODE})


def read_output(root: Path, job: dict[str, Any]) -> Any:
    path = root / job["result"]["path"]
    if path.suffix == ".parquet":
        return pl.read_parquet(path).sort("stock_code").to_dicts()
    return json.loads(path.read_text())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    source = args.data_dir.resolve()
    original = Settings(_env_file=None)
    if not original.tushare_token.get_secret_value():
        print(json.dumps({"passed": False, "reason": "missing_environment_token"}))
        return 2
    with TemporaryDirectory(prefix="ashare-worker-concurrency-") as directory:
        root = Path(directory)
        print(json.dumps({"phase": "copy_catalog"}), flush=True)
        with (
            sqlite3.connect(f"file:{source / 'business.db'}?mode=ro", uri=True) as existing,
            sqlite3.connect(root / "business.db") as copied,
        ):
            existing.backup(copied)
            copied.execute("DELETE FROM sync_checkpoints")
            copied.execute("DELETE FROM job_logs")
            copied.execute("DELETE FROM jobs")
            # Only current versions are read by this acceptance. Their immutable
            # paths point into the read-only mount; new market paths stay local.
            for (version_id,) in copied.execute("SELECT version_id FROM dataset_heads").fetchall():
                manifest = json.loads(
                    copied.execute(
                        "SELECT manifest FROM dataset_versions WHERE id = ?", (version_id,)
                    ).fetchone()[0]
                )
                copied.execute(
                    "UPDATE dataset_versions SET manifest = ? WHERE id = ?",
                    (
                        json.dumps({key: f"baseline/{path}" for key, path in manifest.items()}),
                        version_id,
                    ),
                )
            copied.commit()
        (root / "baseline").symlink_to(source, target_is_directory=True)
        settings = original.model_copy(
            update={
                "data_dir": root,
                "database_path": root / "business.db",
                "api_host": "127.0.0.1",
                "worker_poll_seconds": 0.05,
                "worker_research_concurrency": 2,
            }
        )
        service = ApplicationService(settings)
        worker = Worker(settings, BoundedProvider(settings))
        snapshot = service.db.snapshot()
        assert all(
            service.repository.version(v, False)["source"] == "tushare" for v in snapshot.values()
        )
        end = service.repository.latest_date("daily", snapshot)
        assert end
        calendar = service.repository.read("trade_cal", snapshot, end_date=end).to_dicts()
        days = sorted(
            {
                row["cal_date"]
                for row in calendar
                if row.get("exchange") == "SSE" and row["is_open"] == 1
            }
        )
        start = next(
            day
            for day in days
            if day >= (date.fromisoformat(end) - timedelta(days=365)).isoformat()
        )
        requests: list[tuple[str, Any]] = [
            ("screening", ScreeningRequest(as_of_date=end, rule_id="A")),
            ("screening", ScreeningRequest(as_of_date=end, rule_id="B")),
            (
                "dca",
                DcaRequest(
                    stock_code=CODE,
                    start_date=start,
                    end_date=end,
                    monthly_day=1,
                    monthly_amount="3000",
                    data_versions=snapshot,
                ),
            ),
            (
                "backtest",
                BacktestCompareRequest(
                    stock_code=CODE,
                    start_date=start,
                    end_date=end,
                    initial_cash="100000",
                    data_versions=snapshot,
                    strategies=[
                        StrategyConfig(id=name)
                        for name in [
                            "buy_hold",
                            "ma_cross",
                            "macd_cross",
                            "rsi_reversal",
                            "donchian_breakout",
                            "bollinger_reversion",
                        ]
                    ],
                ),
            ),
        ]

        def submit(kind: str, request: Any) -> dict[str, Any]:
            if kind == "screening":
                return service.submit_screen(request)
            if kind == "dca":
                return service.dca.submit(request)
            return service.backtest.submit_comparison(request)

        print(json.dumps({"phase": "serial_reference", "as_of_date": end}), flush=True)
        reference = []
        for kind, request in requests:
            task = submit(kind, request)
            assert worker.run_once()
            completed = service.db.job(task["id"])
            assert completed["status"] == "succeeded"
            reference.append(read_output(root, completed))

        peaks = {"sync": 0, "research": 0, "combined": 0}
        memory_peaks = {"working_set": 0, "anonymous": 0}
        samples = 0
        sync_terminal = threading.Event()
        stop_monitor = threading.Event()

        def monitor(sync_id: str) -> None:
            nonlocal samples
            while not stop_monitor.wait(0.02):
                with worker.db.engine.connect() as conn:
                    rows = conn.execute(select(jobs.c.id, jobs.c.kind, jobs.c.status)).all()
                active = [row for row in rows if row.status in {"running", "cancel_requested"}]
                sync_count = sum(row.kind == "sync" for row in active)
                research_count = len(active) - sync_count
                peaks["sync"] = max(peaks["sync"], sync_count)
                peaks["research"] = max(peaks["research"], research_count)
                peaks["combined"] = max(peaks["combined"], len(active))
                assert sync_count <= 1 and research_count <= 2
                samples += 1
                cgroup = Path("/sys/fs/cgroup")
                if (cgroup / "memory.current").exists():
                    current = int((cgroup / "memory.current").read_text())
                    statistics = dict(
                        line.split() for line in (cgroup / "memory.stat").read_text().splitlines()
                    )
                    memory_peaks["working_set"] = max(
                        memory_peaks["working_set"], current - int(statistics["inactive_file"])
                    )
                    memory_peaks["anonymous"] = max(
                        memory_peaks["anonymous"], int(statistics["anon"])
                    )
                if any(
                    row.id == sync_id and row.status in {"succeeded", "partial", "failed"}
                    for row in rows
                ):
                    sync_terminal.set()

        for phase, pairs in [
            ("two_full_market_screens", requests[:2]),
            ("dca_and_six_strategies", requests[2:]),
        ]:
            observation_end = date.fromisoformat(end)
            # Pin a bounded full-history recovery state; the real SyncService
            # resumes it without expanding to the full production stock universe.
            sync = service.db.enqueue(
                "sync",
                SyncRequest(
                    mode="dividend", history_refresh="full", end_date=observation_end
                ).model_dump(mode="json"),
                {
                    **snapshot,
                    "_sync_policy": SYNC_POLICY,
                    "_dividend_codes": json.dumps([CODE]),
                    "_dividend_as_of": end,
                    "_dividend_refresh": {
                        "codes": [CODE],
                        "end": end,
                        "start": (observation_end - timedelta(days=30)).isoformat(),
                        "baseline": snapshot,
                        "full": True,
                        "targets": [CODE],
                        "queries": {},
                        "discovery_complete": True,
                        "baseline_full_date": None,
                    },
                },
            )
            tasks = [submit(kind, request) for kind, request in pairs]
            started = time.monotonic()
            sync_terminal.clear()
            stop_monitor.clear()
            worker = Worker(settings, BoundedProvider(settings))
            with ThreadPoolExecutor(max_workers=2) as executor:
                watching = executor.submit(monitor, sync["id"])
                running = executor.submit(worker.run_loop)
                try:
                    deadline = time.monotonic() + 600
                    while time.monotonic() < deadline:
                        values = [service.db.job(task["id"]) for task in tasks]
                        if (
                            all(value["status"] == "succeeded" for value in values)
                            and sync_terminal.is_set()
                        ):
                            break
                        assert all(
                            value["status"] not in {"failed", "cancelled"} for value in values
                        )
                        if running.done():
                            running.result()
                            raise AssertionError("worker exited before completion")
                        time.sleep(0.1)
                    else:
                        raise AssertionError("mixed workload timeout")
                finally:
                    worker.stop()
                    stop_monitor.set()
                    running.result(timeout=60)
                    watching.result(timeout=10)
            result = service.db.job(sync["id"])
            assert result["status"] == "succeeded" and result["result"]["stock_count"] == 1
            offset = 0 if phase == "two_full_market_screens" else 2
            for index, task in enumerate(tasks):
                assert read_output(root, service.db.job(task["id"])) == reference[offset + index]
            # Original immutable versions remain usable after live publication.
            assert service.repository.read("dividend", snapshot, [CODE]).height > 0
            assert service.db.completed(sync["id"], "dividend:observation")
            print(
                json.dumps(
                    {
                        "phase": phase,
                        "seconds": time.monotonic() - started,
                        "serial_results_match": True,
                        "live_sync_status": result["status"],
                    }
                ),
                flush=True,
            )
        assert peaks == {"sync": 1, "research": 2, "combined": 3}
        memory = Path("/sys/fs/cgroup/memory.peak")
        events = Path("/sys/fs/cgroup/memory.events")
        memory_events = (
            {
                key: int(value)
                for key, value in (line.split() for line in events.read_text().splitlines())
            }
            if events.exists()
            else {}
        )
        assert memory_events.get("oom", 0) == memory_events.get("oom_kill", 0) == 0
        print(
            json.dumps(
                {
                    "passed": True,
                    "fixture_mode": False,
                    "as_of_date": end,
                    "live_stock_scope": CODE,
                    "peaks": peaks,
                    "samples": samples,
                    "memory_peak_mib": int(memory.read_text()) / 1024**2
                    if memory.exists()
                    else None,
                    "mixed_working_set_peak_mib": memory_peaks["working_set"] / 1024**2,
                    "mixed_anonymous_peak_mib": memory_peaks["anonymous"] / 1024**2,
                    "memory_events": memory_events,
                    "repeat_sync": "passed",
                    "old_version_read": "passed",
                    "isolated_catalog_and_publications": True,
                }
            ),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProviderError as exc:
        print(json.dumps({"passed": False, "provider_error_code": exc.code}))
        raise SystemExit(1) from None
    except Exception as exc:
        # Never print exception text or tracebacks from a credentialled live run.
        print(json.dumps({"passed": False, "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
