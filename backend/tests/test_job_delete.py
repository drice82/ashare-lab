from pathlib import Path
from unittest.mock import patch

import pytest
from ashare_lab.api import create_app
from ashare_lab.config import Settings
from ashare_lab.db import checkpoints, logs, settings_table
from ashare_lab.fixtures import load_fixture
from ashare_lab.worker import Worker
from fastapi.testclient import TestClient
from sqlalchemy import select


@pytest.fixture
def system(tmp_path: Path):
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        database_path=tmp_path / "db.sqlite",
        data_management_password="test-management",
    )
    app = create_app(settings)
    service = app.state.service
    load_fixture(service.repository)
    return (
        TestClient(app, headers={"X-Data-Management-Password": "test-management"}),
        service,
        settings,
    )


def path(kind: str, job_id: str) -> str:
    return f"/api/v1/data/sync/{job_id}" if kind == "sync" else f"/api/v1/{kind}/runs/{job_id}"


@pytest.mark.parametrize("kind", ["sync", "screening", "dca", "backtest"])
@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled", "partial"])
def test_delete_finished_task_preserves_versions(system, kind, status):
    client, service, settings = system
    snapshot = service.db.snapshot()
    job = service.db.enqueue(kind, {})
    job_id = job["id"]
    service.db.update(job_id, status=status)
    service.db.log(job_id, "info", "test")
    directory = settings.data_dir / "results" / job_id
    directory.mkdir(parents=True)
    (directory / "result.json").write_text("{}")
    with service.db.engine.begin() as conn:
        conn.execute(
            checkpoints.insert().values(job_id=job_id, key="daily", version_id=snapshot["daily"])
        )
        conn.execute(settings_table.insert().values(key=f"slot:{job_id}", value={"job_id": job_id}))
    response = client.delete(path(kind, job_id))
    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": True, "id": job_id}
    assert not directory.exists()
    for table in (logs, checkpoints):
        with service.db.engine.connect() as conn:
            assert conn.execute(select(table).where(table.c.job_id == job_id)).first() is None
    assert client.get(path(kind, job_id)).status_code == 404
    assert client.delete(path(kind, job_id)).status_code == 404
    assert service.db.snapshot() == snapshot
    assert service.repository.read("daily", snapshot).height > 0
    with service.db.engine.connect() as conn:
        assert conn.execute(
            select(settings_table).where(settings_table.c.key == f"slot:{job_id}")
        ).first()
    Worker(settings).recover()
    assert not service.task_list(kind)


@pytest.mark.parametrize("status", ["queued", "running", "cancel_requested"])
def test_active_tasks_cannot_be_deleted(system, status):
    client, service, _ = system
    job = service.db.enqueue("screening", {})
    service.db.update(job["id"], status=status)
    assert client.delete(path("screening", job["id"])).status_code == 409
    assert service.db.job(job["id"])["status"] == status


def test_type_auth_and_cleanup_retry(system):
    client, service, settings = system
    job = service.db.enqueue("sync", {})
    service.db.update(job["id"], status="failed")
    assert client.delete(path("dca", job["id"])).status_code == 404
    assert (
        client.delete(
            path("sync", job["id"]), headers={"X-Data-Management-Password": ""}
        ).status_code
        == 403
    )
    directory = settings.data_dir / "results" / job["id"]
    directory.mkdir(parents=True)
    with patch("ashare_lab.services.shutil.rmtree", side_effect=OSError("test")):
        assert client.delete(path("sync", job["id"])).status_code == 503
    assert service.db.job(job["id"])["status"] == "failed"
    assert client.delete(path("sync", job["id"])).status_code == 200


def test_pagination_stable_and_all_history_accessible(system):
    client, service, _ = system
    for _ in range(105):
        service.db.enqueue("screening", {})
    first = client.get("/api/v1/jobs?kind=screening&limit=100").json()
    second = client.get("/api/v1/jobs?kind=screening&limit=100&offset=100").json()
    assert len(first) == 100 and len(second) == 5
    assert len({job["id"] for job in first + second}) == 105
    assert client.get("/api/v1/jobs?kind=screening&offset=-1").status_code == 422


def test_delete_serializes_with_resume(system):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from ashare_lab.services import ServiceError

    _, service, _ = system
    job = service.db.enqueue("sync", {})
    service.db.update(job["id"], status="failed")
    entered, release, resuming = Event(), Event(), Event()

    def cleanup(_):
        entered.set()
        assert release.wait(5)

    def resume():
        resuming.set()
        with pytest.raises(ServiceError) as error:
            service.resume(job["id"])
        assert error.value.status == 404

    with patch("ashare_lab.services.shutil.rmtree", side_effect=cleanup):
        with ThreadPoolExecutor(max_workers=2) as executor:
            deletion = executor.submit(service.delete_job, job["id"], "sync")
            assert entered.wait(5)
            recovery = executor.submit(resume)
            assert resuming.wait(5)
            release.set()
            assert deletion.result(timeout=5)["deleted"]
            recovery.result(timeout=5)


def test_real_worker_result_deleted_and_exports_inaccessible(system):
    from ashare_lab.fixtures import FIXTURE_DATE

    client, service, settings = system
    job_id = client.post(
        "/api/v1/screening/run", json={"as_of_date": FIXTURE_DATE, "rule_id": "A"}
    ).json()["id"]
    worker = Worker(settings)
    assert worker.run_once()
    task = service.db.job(job_id)
    assert task["status"] == "succeeded"
    assert task["logs"][-1]["message"] == "任务已完成"
    assert (settings.data_dir / task["result"]["path"]).is_file()
    assert client.delete(path("screening", job_id)).status_code == 200
    assert client.get(path("screening", job_id) + "/results").status_code == 404
    assert client.get(path("screening", job_id) + "/export").status_code == 404
    assert not worker.run_once()


def test_deleted_scheduled_job_does_not_requeue_same_slot(system):
    from datetime import datetime

    from ashare_lab.scheduler import TZ, DailyNameScheduler

    _, service, settings = system
    scheduler = DailyNameScheduler(settings, service.db, service.repository)
    instant = datetime(2026, 10, 10, 3, tzinfo=TZ)
    with patch.object(scheduler, "enabled", return_value=True):
        scheduler.tick(instant)
        job = service.task_list("sync")[0]
        service.db.update(job["id"], status="failed")
        service.delete_job(job["id"], "sync")
        scheduler.tick(instant)
    assert not service.task_list("sync")


def test_research_delete_requires_access_token(system):
    client, service, settings = system
    from pydantic import SecretStr

    settings.api_access_token = SecretStr("test-access")
    job = service.db.enqueue("screening", {})
    service.db.update(job["id"], status="failed")
    assert client.delete(path("screening", job["id"])).status_code == 401
    assert (
        client.delete(
            path("screening", job["id"]), headers={"Authorization": "Bearer test-access"}
        ).status_code
        == 200
    )


def test_worker_completion_and_delete_are_serialized(system):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from ashare_lab.fixtures import FIXTURE_DATE

    client, service, settings = system
    job_id = client.post(
        "/api/v1/screening/run", json={"as_of_date": FIXTURE_DATE, "rule_id": "A"}
    ).json()["id"]
    worker = Worker(settings)
    finalizing, release, deleting = Event(), Event(), Event()
    original_log = worker.db.log

    def log(job_id, level, message, connection=None):
        if message == "任务已完成":
            finalizing.set()
            assert release.wait(5)
        original_log(job_id, level, message, connection=connection)

    def delete():
        deleting.set()
        return service.delete_job(job_id, "screening")

    with patch.object(worker.db, "log", side_effect=log):
        with ThreadPoolExecutor(max_workers=2) as executor:
            run = executor.submit(worker.run_once)
            assert finalizing.wait(10)
            deletion = executor.submit(delete)
            assert deleting.wait(5)
            release.set()
            assert run.result(timeout=5)
            assert deletion.result(timeout=5)["deleted"]
    assert not (settings.data_dir / "results" / job_id).exists()
    assert not service.task_list("screening")


def test_interrupted_file_cleanup_can_be_retried_after_restart(system):
    client, service, settings = system
    job = service.db.enqueue("screening", {})
    service.db.update(job["id"], status="failed")
    directory = settings.data_dir / "results" / job["id"]
    directory.mkdir(parents=True)
    artifact = directory / "unpublished.json"
    artifact.write_text("{}")

    def interrupted(_):
        artifact.unlink()
        raise OSError("interrupted cleanup")

    with patch("ashare_lab.services.shutil.rmtree", side_effect=interrupted):
        assert client.delete(path("screening", job["id"])).status_code == 503
    Worker(settings).recover()
    assert service.db.job(job["id"])["status"] == "failed"
    assert client.delete(path("screening", job["id"])).status_code == 200
    assert not directory.exists()


def test_result_symlink_cannot_delete_other_files(system, tmp_path):
    client, service, settings = system
    job = service.db.enqueue("screening", {})
    service.db.update(job["id"], status="failed")
    outside = tmp_path / "other-task"
    outside.mkdir()
    result = outside / "result.json"
    result.write_text("{}")
    directory = settings.data_dir / "results" / job["id"]
    directory.parent.mkdir(parents=True)
    directory.symlink_to(outside, target_is_directory=True)
    assert client.delete(path("screening", job["id"])).status_code == 503
    assert result.exists()
    assert service.db.job(job["id"])["status"] == "failed"
