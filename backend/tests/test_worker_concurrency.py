"""Hand-checkable lane limits, task isolation and pinned-result regressions."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event, Lock
from unittest.mock import patch

import polars as pl
import pytest
from ashare_lab.config import Settings
from ashare_lab.fixtures import FIXTURE_DATE
from ashare_lab.providers import ProviderError
from ashare_lab.schemas import ScreeningRequest
from ashare_lab.screening import ScreeningEngine
from ashare_lab.services import ApplicationService
from ashare_lab.worker import Worker
from test_backtest_api import payload, system  # noqa: F401


@contextmanager
def running(worker):
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker.run_loop)
        try:
            yield future
        finally:
            worker.stop()
            future.result(timeout=15)


@pytest.mark.parametrize("value", [0, 3, -1])
def test_research_limit_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="WORKER_RESEARCH_CONCURRENCY"):
        Settings(_env_file=None, worker_research_concurrency=value)


def test_research_limit_default_and_environment(monkeypatch):
    assert Settings(_env_file=None).worker_research_concurrency == 2
    monkeypatch.setenv("WORKER_RESEARCH_CONCURRENCY", "1")
    assert Settings(_env_file=None).worker_research_concurrency == 1


def test_two_research_and_one_sync_lanes_do_not_block_each_other(repository):
    settings = Settings(
        _env_file=None,
        data_dir=repository.root,
        database_path=repository.root / "business.db",
        worker_poll_seconds=0.01,
    )
    worker = Worker(settings)
    service = ApplicationService(settings)
    syncs = [worker.db.enqueue("sync", {}) for _ in range(2)]
    screens = [
        service.submit_screen(ScreeningRequest(as_of_date=FIXTURE_DATE, rule_id=rule))
        for rule in ["A", "B", "C"]
    ]
    entered = {job["id"]: Event() for job in syncs + screens}
    release = {job["id"]: Event() for job in syncs + screens}
    original = worker.execute_job
    active = {"sync": 0, "research": 0}
    peak = dict(active)
    guard = Lock()

    def execute(job):
        lane = "sync" if job["kind"] == "sync" else "research"
        with guard:
            active[lane] += 1
            peak[lane] = max(peak[lane], active[lane])
        entered[job["id"]].set()
        assert release[job["id"]].wait(10)
        try:
            original(job)
        finally:
            with guard:
                active[lane] -= 1

    # Sync payloads deliberately fail validation after release; research runs the
    # real engine. A failed sync must neither occupy the lane nor stop research.
    with patch.object(worker, "execute_job", side_effect=execute):
        try:
            with running(worker):
                for job in [syncs[0], *screens[:2]]:
                    assert entered[job["id"]].wait(5)
                    assert worker.db.job(job["id"])["status"] == "running"
                for job in [syncs[1], screens[2]]:
                    assert not entered[job["id"]].is_set()
                    assert worker.db.job(job["id"])["status"] == "queued"
                release[screens[0]["id"]].set()
                assert entered[screens[2]["id"]].wait(5)
                assert not entered[syncs[1]["id"]].is_set()
                release[syncs[0]["id"]].set()
                assert entered[syncs[1]["id"]].wait(5)
                for event in release.values():
                    event.set()
        finally:
            for event in release.values():
                event.set()
    assert peak == {"sync": 1, "research": 2}
    assert [worker.db.job(job["id"])["status"] for job in syncs] == ["failed"] * 2
    for job in screens:
        actual = worker.db.job(job["id"])
        assert actual["status"] == "succeeded"
        reference = ScreeningEngine(repository).run(
            ScreeningRequest.model_validate(job["payload"]), job["snapshot"]
        )
        assert actual["result"]["total"] == len(reference)
        assert actual["result"]["matched"] == sum(
            row["match_status"] == "matched" for row in reference
        )


def test_cancel_failure_heartbeat_and_stop_are_isolated(repository):
    settings = Settings(
        _env_file=None,
        data_dir=repository.root,
        database_path=repository.root / "business.db",
        worker_poll_seconds=0.01,
    )
    worker = Worker(settings)
    service = ApplicationService(settings)
    tasks = [
        service.submit_screen(ScreeningRequest(as_of_date=FIXTURE_DATE, rule_id=rule))
        for rule in ["A", "B", "C"]
    ]
    arrived = {rule: Event() for rule in ["A", "B", "C"]}
    releases = {rule: Event() for rule in arrived}
    observed = Event()
    original_update = worker.db.update
    original_run = ScreeningEngine.run

    def update(job_id, **values):
        original_update(job_id, **values)
        if job_id == tasks[1]["id"] and "heartbeat" in values:
            observed.set()

    def run(engine, request, snapshot, check, progress):
        arrived[request.rule_id].set()
        assert releases[request.rule_id].wait(10)
        if request.rule_id == "C":
            raise ProviderError("test_failure", "isolated failure")
        return original_run(engine, request, snapshot, check, progress)

    with patch.object(worker.db, "update", side_effect=update):
        with patch.object(ScreeningEngine, "run", run):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(worker.run_loop)
                try:
                    assert arrived["A"].wait(5) and arrived["B"].wait(5)
                    assert observed.wait(5)
                    service.cancel(tasks[0]["id"], "screening")
                    releases["A"].set()
                    assert arrived["C"].wait(5)
                    waiting = service.submit_screen(
                        ScreeningRequest(as_of_date=FIXTURE_DATE, rule_id="D")
                    )
                    worker.stop()
                    releases["B"].set()
                    releases["C"].set()
                    future.result(timeout=10)
                finally:
                    worker.stop()
                    for event in releases.values():
                        event.set()
    assert [worker.db.job(job["id"])["status"] for job in tasks] == [
        "cancelled",
        "succeeded",
        "failed",
    ]
    assert worker.db.job(waiting["id"])["status"] == "queued"
    assert all(worker.db.job(job["id"])["logs"][-1]["level"] for job in tasks)


def test_atomic_claim_and_multiple_task_recovery(repository):
    settings = Settings(
        _env_file=None, data_dir=repository.root, database_path=repository.root / "business.db"
    )
    worker = Worker(settings)
    jobs = [
        worker.db.enqueue(kind, {}, repository.db.snapshot())
        for kind in ["sync", "screening", "backtest"]
    ]
    with ThreadPoolExecutor(max_workers=6) as pool:
        claimed = list(pool.map(lambda _: worker.claim_job(), range(6)))
    assert {job["id"] for job in claimed if job} == {job["id"] for job in jobs}
    assert sum(job is not None for job in claimed) == 3
    worker.db.update(jobs[2]["id"], cancel_requested=True, status="cancel_requested")
    worker.recover()
    assert [worker.db.job(job["id"])["status"] for job in jobs] == ["queued", "queued", "cancelled"]
    assert worker.db.job(jobs[1]["id"])["snapshot"]["_recovery_interruptions"] == 1
    for job in jobs:
        actual = worker.db.job(job["id"])
        assert actual["attempt"] == 1
        assert {
            key: value for key, value in actual["snapshot"].items() if not key.startswith("_")
        } == job["snapshot"]


def test_parallel_dca_comparison_matches_serial_and_pinned_versions(system):  # noqa: F811
    client, worker, service = system
    worker.settings.worker_poll_seconds = 0.01
    body = payload(client)
    compare_body = {
        **body,
        "strategies": [
            {"id": name}
            for name in [
                "buy_hold",
                "ma_cross",
                "macd_cross",
                "rsi_reversal",
                "donchian_breakout",
                "bollinger_reversion",
            ]
        ],
    }
    dca_body = {key: value for key, value in body.items() if key != "initial_cash"}
    dca_body.update(monthly_day=3, monthly_amount="3000")
    serial = []
    for route, request in [("backtest/compare", compare_body), ("dca/run", dca_body)]:
        response = client.post(f"/api/v1/{route}", json=request)
        assert response.status_code == 202, response.text
        job = response.json()
        assert worker.run_once()
        serial.append(service.db.job(job["id"]))
    parallel = [
        client.post(f"/api/v1/{route}", json=request).json()
        for route, request in [("backtest/compare", compare_body), ("dca/run", dca_body)]
    ]
    # Publish changed data after submission; both concurrent runs must retain
    # their pinned original snapshot, including full ledger and strategy output.
    frame = service.repository.read("daily", body["data_versions"]).with_columns(
        pl.lit(999.0).alias("open")
    )
    service.repository.publish("daily", frame, "revision", "fixture", "new")
    completed = Event()
    original = worker.execute_job
    count = 0
    guard = Lock()

    def execute(job):
        nonlocal count
        original(job)
        with guard:
            count += 1
            if count == 2:
                completed.set()

    with patch.object(worker, "execute_job", side_effect=execute):
        with running(worker):
            assert completed.wait(10)
    import json

    for before, after in zip(serial, parallel, strict=True):
        actual = service.db.job(after["id"])
        assert actual["status"] == "succeeded"
        assert json.loads(
            (worker.settings.data_dir / before["result"]["path"]).read_text()
        ) == json.loads((worker.settings.data_dir / actual["result"]["path"]).read_text())


def test_once_keeps_single_job_semantics_and_worker_lock(repository):
    settings = Settings(
        _env_file=None, data_dir=repository.root, database_path=repository.root / "business.db"
    )
    worker = Worker(settings)
    tasks = [worker.db.enqueue("unsupported", {}) for _ in range(2)]
    worker.serve(once=True)
    assert worker.db.job(tasks[0]["id"])["status"] == "failed"
    assert worker.db.job(tasks[1]["id"])["status"] == "queued"
    import fcntl

    with settings.database_path.with_suffix(".worker.lock").open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="只允许一个 Worker"):
            worker.serve(once=True)


@pytest.mark.parametrize("cancel", [False, True])
def test_executor_submission_failure_releases_claim(repository, cancel):
    settings = Settings(
        _env_file=None, data_dir=repository.root, database_path=repository.root / "business.db"
    )
    worker = Worker(settings)
    service = ApplicationService(settings)
    task = worker.db.enqueue("screening", {})

    def failed_submit(*args, **kwargs):
        if cancel:
            service.cancel(task["id"], "screening")
        raise RuntimeError("executor unavailable")

    with patch("ashare_lab.worker.ThreadPoolExecutor.submit", side_effect=failed_submit):
        with pytest.raises(RuntimeError, match="executor unavailable"):
            worker.run_loop()
    value = worker.db.job(task["id"])
    assert value["status"] == ("cancelled" if cancel else "queued")
    assert value["attempt"] == 0
    assert value["heartbeat"] is None


def test_history_completion_has_no_writes_after_terminal_publication(tmp_path):
    from ashare_lab.schemas import SyncRequest
    from test_sync_recovery import MiniProvider

    settings = Settings(_env_file=None, data_dir=tmp_path, database_path=tmp_path / "business.db")
    worker = Worker(settings, MiniProvider())
    service = ApplicationService(settings)
    task = service.submit_sync(
        SyncRequest(mode="dividend", history_refresh="full", end_date=FIXTURE_DATE)
    )
    original = worker.repository.publish
    deleted = Event()

    def publish(*args, **kwargs):
        version = original(*args, **kwargs)
        if kwargs.get("complete_job"):
            value = service.db.job(task["id"])
            assert value["status"] == "succeeded"
            assert value["logs"][-1]["message"] == "任务已完成"
            # Once terminal status is visible the API may delete immediately.
            # Worker must perform no subsequent heartbeat, log or task read.
            assert service.delete_job(task["id"], "sync")["deleted"]
            deleted.set()
        return version

    with patch.object(worker.repository, "publish", side_effect=publish):
        assert worker.run_once()
    assert deleted.is_set()
    assert not service.task_list("sync")


@pytest.mark.parametrize("forced", [False, True])
def test_process_shutdown_drains_or_recovers_all_lanes(repository, forced):
    import os
    import signal
    import subprocess
    import sys
    import time
    from pathlib import Path

    settings = Settings(
        _env_file=None, data_dir=repository.root, database_path=repository.root / "business.db"
    )
    worker = Worker(settings)
    tasks = [
        worker.db.enqueue(kind, {}, repository.db.snapshot())
        for kind in ["sync", "screening", "backtest"]
    ]
    program = """
from pathlib import Path
import time
from ashare_lab.config import Settings
from ashare_lab.worker import Worker
settings = Settings(_env_file=None)
root = settings.data_dir
worker = Worker(settings)
original_execute, original_stop = worker.execute_job, worker.stop
def execute(job):
    (root / (job['id'] + '.started')).touch()
    while not (root / 'release').exists():
        time.sleep(0.01)
    original_execute(job)
def stop():
    original_stop()
    (root / 'stopped').touch()
worker.execute_job, worker.stop = execute, stop
worker.serve()
"""
    env = {
        key: value for key, value in os.environ.items() if key.lower() not in Settings.model_fields
    }
    env.update(
        DATA_DIR=str(repository.root),
        DATABASE_PATH=str(settings.database_path),
        PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"),
        WORKER_POLL_SECONDS="0.01",
        POLARS_MAX_THREADS="2",
    )
    process = subprocess.Popen(
        [sys.executable, "-c", program], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )

    def wait_for(predicate):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if predicate():
                return
            assert process.poll() is None, process.communicate()
            time.sleep(0.01)
        raise AssertionError("child worker did not reach the expected boundary")

    try:
        wait_for(
            lambda: all((repository.root / (job["id"] + ".started")).exists() for job in tasks)
        )
        assert all(worker.db.job(job["id"])["status"] == "running" for job in tasks)
        with pytest.raises(RuntimeError, match="只允许一个 Worker"):
            worker.serve(once=True)
        waiting = worker.db.enqueue("screening", {})
        process.send_signal(signal.SIGKILL if forced else signal.SIGTERM)
        if not forced:
            wait_for(lambda: (repository.root / "stopped").exists())
            assert process.poll() is None  # lock held until active tasks drain
            (repository.root / "release").touch()
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == (-signal.SIGKILL if forced else 0), (stdout, stderr)
        assert worker.db.job(waiting["id"])["status"] == "queued"
        if forced:
            worker.recover()
            assert all(worker.db.job(job["id"])["status"] == "queued" for job in tasks)
            assert worker.db.job(tasks[1]["id"])["snapshot"]["_recovery_interruptions"] == 1
            assert worker.db.job(tasks[2]["id"])["snapshot"]["_recovery_interruptions"] == 1
        else:
            assert all(worker.db.job(job["id"])["status"] == "failed" for job in tasks)
        # The process lock was released in either case; --once still runs one.
        worker.serve(once=True)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)


def test_observation_completion_serializes_concurrent_cancel(repository):
    from ashare_lab.providers import normalize
    from ashare_lab.services import ServiceError
    from sqlalchemy import event

    task = repository.db.enqueue("sync", {})
    settings = Settings(
        _env_file=None, data_dir=repository.root, database_path=repository.root / "business.db"
    )
    service = ApplicationService(settings)
    attempted, responded = Event(), Event()
    triggered = False
    outcome = []

    def cancel():
        attempted.set()
        try:
            service.cancel(task["id"], "sync")
            outcome.append("accepted")
        except ServiceError as exc:
            outcome.append(exc.code)
        responded.set()

    with ThreadPoolExecutor(max_workers=1) as pool:

        def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            nonlocal triggered
            if "SELECT jobs.cancel_requested" in statement and not triggered:
                triggered = True
                pool.submit(cancel)
                assert attempted.wait(2)
                # Cancellation must not be accepted between the last cancellation
                # check and committing a succeeded task. It waits for publication,
                # then observes terminal status and is rejected.
                responded.wait(0.5)

        event.listen(repository.db.engine, "after_cursor_execute", after_cursor_execute)
        try:
            repository.publish(
                "dividend",
                normalize("dividend", pl.DataFrame(), "fixture", task["id"]),
                "history:observation",
                "fixture",
                task["id"],
                checkpoint=(task["id"], "dividend:observation"),
                complete_job=(task["id"], {"warnings": []}),
            )
        finally:
            event.remove(repository.db.engine, "after_cursor_execute", after_cursor_execute)
    assert triggered and responded.is_set()
    assert repository.db.job(task["id"])["status"] == "succeeded"
    assert outcome == ["task_finished"]


def test_research_cancel_during_heartbeat_drain_is_honoured(repository):
    import threading

    settings = Settings(
        _env_file=None, data_dir=repository.root, database_path=repository.root / "business.db"
    )
    worker = Worker(settings)
    service = ApplicationService(settings)
    task = service.submit_screen(ScreeningRequest(as_of_date=FIXTURE_DATE, rule_id="A"))
    original = threading.Thread.join
    cancelled = False

    def join(thread, *args, **kwargs):
        nonlocal cancelled
        if not cancelled and thread.name.endswith("(heartbeat)"):
            cancelled = True
            assert service.cancel(task["id"], "screening")["status"] == "cancel_requested"
        return original(thread, *args, **kwargs)

    with patch.object(threading.Thread, "join", join):
        assert worker.run_once()
    value = worker.db.job(task["id"])
    assert cancelled and value["status"] == "cancelled"
    assert value["logs"][-1]["level"] == "warning"
