"""Human-checkable lists: ownership, revisions, old sessions and pinned research."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from ashare_lab.api import create_app
from ashare_lab.config import Settings
from ashare_lab.db import Database
from ashare_lab.fixtures import load_fixture
from ashare_lab.worker import Worker
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import event, text

PREFIX = "/api/v1/watchlists"
AUTH = "/api/v1/notifications"
ORIGIN = {"Origin": "http://testserver"}


class CapturedCodes(list[str]):
    def __repr__(self) -> str:
        return "<private fixture verification capture>"


@pytest.fixture
def system(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "business.db",
        data_dir=tmp_path,
        notification_cookie_secure=False,
        notification_encryption_key=Fernet.generate_key().decode(),
    )
    app = create_app(settings)
    sent = CapturedCodes()
    monkeypatch.setattr(
        app.state.notifications, "send_verification", lambda address, code: sent.append(code)
    )

    def login(address="alice@fixture.example.org"):
        client = TestClient(app)
        app.state.notifications.start_login(address, limited=False)
        response = client.post(
            AUTH + "/auth/email/verify",
            json={"address": address, "code": sent[-1]},
            headers=ORIGIN,
        )
        assert response.status_code == 200
        return client

    return app, settings, login, sent


def create(client, name="银行", codes=None):
    return client.post(
        PREFIX,
        json={"name": name, "codes": codes or ["000001.SZ", "600000.SH", "000001.SZ"]},
        headers=ORIGIN,
    )


def test_ownership_revisions_validation_and_multi_device(system):
    app, _, login, _ = system
    anonymous = TestClient(app)
    assert anonymous.get(PREFIX).status_code == 401
    alice, second, bob = login(), login(), login("bob@fixture.example.org")
    response = create(alice, " 银行 ")
    assert response.status_code == 201
    row = response.json()
    assert row["name"] == "银行"
    assert row["codes"] == ["000001.SZ", "600000.SH"]
    assert row["revision"] == 1
    assert "owner_id" not in row
    assert second.get(PREFIX).json() == [row]
    assert bob.get(PREFIX).json() == []
    route = PREFIX + "/" + row["id"]
    assert bob.get(route).status_code == 404
    assert bob.put(route, json={**row, "name": "偷改"}, headers=ORIGIN).status_code == 422
    payload = {"name": "新银行", "codes": [], "revision": 1}
    assert bob.put(route, json=payload, headers=ORIGIN).status_code == 404
    assert bob.delete(route + "?revision=1", headers=ORIGIN).status_code == 404
    changed = alice.put(route, json=payload, headers=ORIGIN)
    assert changed.status_code == 200
    assert changed.json()["codes"] == [] and changed.json()["revision"] == 2
    assert second.put(route, json=payload, headers=ORIGIN).status_code == 409
    assert second.delete(route + "?revision=1", headers=ORIGIN).status_code == 409
    assert create(alice, "新银行").status_code == 409
    assert create(bob, "新银行").status_code == 201
    for name, codes in [
        (" ", []),
        ("x" * 81, []),
        ("非法", ["600000.SZ"]),
        ("非法", ["000001.SH"]),
        ("非法", ["430001.BJ"]),
        ("非法", ["600000.SH"] * 6001),
    ]:
        assert (
            alice.post(PREFIX, json={"name": name, "codes": codes}, headers=ORIGIN).status_code
            == 422
        )
    assert alice.delete(route + "?revision=2", headers=ORIGIN).status_code == 200
    assert second.get(route).status_code == 404


def test_origin_shared_key_and_no_store(system):
    app, settings, login, _ = system
    settings.api_access_token = SecretStr("isolated-test-key")
    alice = login()
    assert alice.get(PREFIX).status_code == 200
    assert alice.get(PREFIX).headers["Cache-Control"] == "no-store"
    assert alice.get(PREFIX + "/unknown/path").status_code == 401
    assert alice.get("/api/v1/backtest/options").status_code == 401
    body = {"name": "银行", "codes": []}
    assert alice.post(PREFIX, json=body).status_code == 403
    assert (
        alice.post(PREFIX, json=body, headers={"Origin": "https://evil.example"}).status_code == 403
    )
    assert alice.post(PREFIX, json=body, headers=ORIGIN).status_code == 201
    # List updates use write limits, never SMTP send limits.
    row = alice.get(PREFIX).json()[0]
    for _ in range(12):
        response = alice.put(
            PREFIX + "/" + row["id"],
            json={"name": "银行", "codes": [], "revision": row["revision"]},
            headers=ORIGIN,
        )
        assert response.status_code == 200
        row = response.json()


def test_legacy_session_promotion_logout_and_email_change(system):
    app, _, login, sent = system
    alice = login()
    assert alice.cookies.get("ashare_user_session")
    first = create(alice).json()
    other = login()
    value = alice.cookies.get("ashare_user_session")
    alice.cookies.clear()
    alice.cookies.set("ashare_notification_session", value, domain="testserver.local", path=AUTH)
    assert alice.get(PREFIX).status_code == 401
    alice.cookies.set(
        "ashare_user_session", "invalid-isolated-session", domain="testserver.local", path="/api/v1"
    )
    assert alice.get(AUTH + "/session").status_code == 401
    alice.cookies.delete("ashare_user_session", domain="testserver.local", path="/api/v1")
    before = app.state.notifications.session_info(value)
    response = alice.get(AUTH + "/session")
    assert response.status_code == 200
    assert response.json()["expires_at"] == before["expires_at"]
    cookie_preserved = alice.cookies.get("ashare_user_session") == value
    assert cookie_preserved
    assert not alice.cookies.get("ashare_notification_session")
    assert alice.get(PREFIX).json() == [first]
    app.state.notifications.start_email_change(
        before["owner_id"], "new@fixture.example.org", limited=False
    )
    changed = alice.post(
        AUTH + "/account/email/verify",
        json={"address": "new@fixture.example.org", "code": sent[-1]},
        headers=ORIGIN,
    )
    assert changed.status_code == 200
    assert alice.get(PREFIX).json() == [first]
    assert other.get(PREFIX).status_code == 401
    assert alice.delete(AUTH + "/session", headers=ORIGIN).status_code == 200
    assert alice.get(PREFIX).status_code == 401
    assert login("new@fixture.example.org").get(PREFIX).json() == [first]


def test_saved_list_changes_do_not_change_jobs_or_recovery(system):
    app, settings, login, _ = system
    load_fixture(app.state.service.repository)
    alice = login()
    row = create(alice, codes=["000001.SZ", "000002.SZ"]).json()
    versions = alice.get("/api/v1/portfolio/options").json()["data_versions"]
    portfolio = alice.post(
        "/api/v1/portfolio/run",
        json={
            "members": [{"stock_code": c} for c in row["codes"]],
            "start_date": "2024-06-03",
            "end_date": "2024-06-28",
            "data_versions": versions,
        },
    ).json()
    screen = alice.post(
        "/api/v1/screening/run",
        json={
            "as_of_date": "2024-06-28",
            "universe": {"type": "custom", "codes": row["codes"]},
            "rule_id": "A",
        },
    ).json()
    originals = [app.state.service.db.job(job["id"]) for job in [portfolio, screen]]
    route = PREFIX + "/" + row["id"]
    assert (
        alice.put(
            route, json={"name": "改后", "codes": ["600000.SH"], "revision": 1}, headers=ORIGIN
        ).status_code
        == 200
    )
    assert alice.delete(route + "?revision=2", headers=ORIGIN).status_code == 200
    worker = Worker(settings)
    assert worker.run_once() and worker.run_once()
    result_routes = [f"/api/v1/{job['kind']}/runs/{job['id']}/results" for job in originals]

    def research_results():
        values = [alice.get(route).json() for route in result_routes]
        # Recovery atomically publishes a new file; economic values stay fixed.
        values[1]["summary"].pop("path", None)
        values[1]["data_versions"].pop("_recovery_interruptions", None)
        return values

    results = research_results()
    for original in originals:
        actual = app.state.service.db.job(original["id"])
        assert actual["status"] == "succeeded"
        assert (
            actual["payload"] == original["payload"] and actual["snapshot"] == original["snapshot"]
        )
        app.state.service.db.update(original["id"], status="running", result=None)
    worker.recover()
    assert worker.run_once() and worker.run_once()
    assert research_results() == results


def test_migration_interruption_retry_and_old_records(tmp_path: Path):
    db = Database(tmp_path / "upgrade.db")
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).parents[1] / "src/ashare_lab/migrations")
    )
    with db.engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "0008")
        conn.execute(
            text("INSERT INTO settings (key, value) VALUES ('existing', :value)"),
            {"value": '{"x":42}'},
        )

    def interrupt(conn, cursor, statement, parameters, context, executemany):
        if "CREATE INDEX ix_watchlists_owner" in statement:
            raise RuntimeError("interrupted migration")

    event.listen(db.engine, "before_cursor_execute", interrupt)
    with pytest.raises(RuntimeError, match="interrupted migration"):
        db.initialize()
    event.remove(db.engine, "before_cursor_execute", interrupt)
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0008"
        assert (
            conn.execute(text("SELECT name FROM sqlite_master WHERE name='watchlists'")).first()
            is None
        )
    db.initialize()
    db.initialize()
    with db.engine.connect() as conn:
        assert (
            conn.execute(text("SELECT value FROM settings WHERE key='existing'")).scalar_one()
            == '{"x":42}'
        )
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0009"
        assert conn.execute(text("PRAGMA foreign_key_check")).all() == []
