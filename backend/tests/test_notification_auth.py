"""Checkable examples for passwordless login and preservation of existing notifications."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from ashare_lab.api import create_app
from ashare_lab.config import Settings
from ashare_lab.db import Database, heads, identifier, versions
from ashare_lab.notification_models import owners
from ashare_lab.notifications import NotificationService, SubscriptionRequest, digest
from ashare_lab.schemas import StrategyConfig
from ashare_lab.services import ServiceError
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text


@pytest.fixture
def auth_service(repository, monkeypatch):
    service = NotificationService(
        Settings(
            _env_file=None,
            notification_encryption_key=Fernet.generate_key().decode(),
            notification_cookie_secure=False,
        ),
        repository.db,
        repository,
    )
    sent = []
    monkeypatch.setattr(
        service, "send_verification", lambda address, code: sent.append((address, code))
    )
    return service, sent


def login(service, sent, address="alice@example.org"):
    service.start_login(address, limited=False)
    return service.login(address, sent[-1][1])


def subscription(service, owner, kinds=None):
    return service.save_subscription(
        owner,
        SubscriptionRequest(
            stock_code="000001.SZ",
            strategy=StrategyConfig(id="ma_cross"),
            channels=kinds or ["email"],
        ),
    )


def test_first_login_multi_device_logout_and_normalization(auth_service):
    s, sent = auth_service
    first = login(s, sent, " Alice@Example.org ")
    sub = subscription(s, first["owner_id"])
    before = s.get_channel(first["owner_id"], "email")
    second = login(s, sent)
    assert second["owner_id"] == first["owner_id"]
    assert s.authenticate(first["session"]) == s.authenticate(second["session"])
    assert s.list_subscriptions(second["owner_id"])[0]["id"] == sub["id"]
    assert s.get_channel(first["owner_id"], "email") == before
    other = login(s, sent, "alice+other@example.org")
    assert other["owner_id"] != first["owner_id"]
    with pytest.raises(ServiceError):
        s.patch_subscription(other["owner_id"], sub["id"], {"status": "paused"})
    s.logout(first["session"])
    s.logout(first["session"])
    with pytest.raises(ServiceError):
        s.authenticate(first["session"])
    assert s.authenticate(second["session"]) == second["owner_id"]


def test_codes_resend_expiry_attempts_and_single_use(auth_service):
    from ashare_lab.notification_models import auth_challenges

    s, sent = auth_service
    s.start_login("alice@example.org", limited=False)
    old = sent[-1][1]
    s.start_login("alice@example.org", limited=False)
    new = sent[-1][1]
    assert old != new
    with pytest.raises(ServiceError):
        s.login("alice@example.org", old)
    s.login("alice@example.org", new)
    with pytest.raises(ServiceError):
        s.login("alice@example.org", new)
    s.start_login("bob@example.org", limited=False)
    for _ in range(5):
        with pytest.raises(ServiceError):
            s.login("bob@example.org", "not-six-digits")
    with pytest.raises(ServiceError):
        s.login("bob@example.org", sent[-1][1])
    s.start_login("expired@example.org", limited=False)
    with s.db.engine.begin() as conn:
        conn.execute(auth_challenges.update().values(expires_at="2000-01-01"))
    with pytest.raises(ServiceError):
        s.login("expired@example.org", sent[-1][1])


def test_email_change_is_atomic_preserves_webhook_and_rejects_old_sessions(auth_service):
    s, sent = auth_service
    first = login(s, sent)
    second = login(s, sent)
    owner = first["owner_id"]
    s._save_channel(owner, "webhook", "device", True)
    sub = subscription(s, owner, ["email", "webhook"])
    s.record_check(sub, "2026-10-09", {"status": "checked", "direction": "buy"})
    old_channel = s.get_channel(owner, "email")
    token = s.unsubscribe_token(sub, old_channel)
    webhook = s.get_channel(owner, "webhook")
    s.start_email_change(owner, "new@example.org", limited=False)
    assert s.get_channel(owner, "email") == old_channel
    with pytest.raises(ServiceError):
        s.login("new@example.org", sent[-1][1])
    changed = s.change_email(owner, "new@example.org", sent[-1][1])
    assert changed["owner_id"] == owner
    assert s.get_channel(owner, "webhook") == webhook
    assert {r["channel"]: r["status"] for r in s.list_deliveries(owner)["items"]} == {
        "email": "cancelled",
        "webhook": "pending",
    }
    assert s.authenticate(changed["session"]) == owner
    for session in (first["session"], second["session"]):
        with pytest.raises(ServiceError):
            s.authenticate(session)
    with pytest.raises(ServiceError):
        s.unsubscribe(token)
    assert login(s, sent, "new@example.org")["owner_id"] == owner
    assert login(s, sent)["owner_id"] != owner


def test_change_email_collision_and_owner_purpose_isolation(auth_service):
    s, sent = auth_service
    first = login(s, sent)
    second = login(s, sent, "bob@example.org")
    channel = s.get_channel(first["owner_id"], "email")
    s.start_email_change(first["owner_id"], "bob@example.org", limited=False)
    code = sent[-1][1]
    with pytest.raises(ServiceError):
        s.change_email(second["owner_id"], "bob@example.org", code)
    with pytest.raises(ServiceError) as error:
        s.change_email(first["owner_id"], "bob@example.org", code)
    assert error.value.code == "email_in_use"
    assert s.get_channel(first["owner_id"], "email") == channel
    assert s.authenticate(first["session"]) == first["owner_id"]
    s.start_login("unused@example.org", limited=False)
    with pytest.raises(ServiceError):
        s.change_email(first["owner_id"], "unused@example.org", sent[-1][1])


def test_disabled_mail_and_unsubscribe_are_not_restored_by_login(auth_service):
    s, sent = auth_service
    first = login(s, sent)
    owner = first["owner_id"]
    sub = subscription(s, owner)
    s.unsubscribe(s.unsubscribe_token(sub, s.get_channel(owner, "email")))
    s.delete_channel(owner, "email")
    before = s.get_channel(owner, "email")
    login(s, sent)
    assert s.get_channel(owner, "email") == before
    assert s.list_subscriptions(owner)[0]["channels"] == []
    s.start_email_change(owner, "new@example.org", limited=False)
    s.change_email(owner, "new@example.org", sent[-1][1])
    assert s.get_channel(owner, "email")["status"] == "disabled"
    s.enable_email(owner)
    assert s.get_channel(owner, "email")["verified"]
    assert s.list_subscriptions(owner)[0]["status"] == "paused"


def test_send_failure_and_database_do_not_expose_secrets(auth_service, monkeypatch):
    from ashare_lab.notification_models import accounts, auth_challenges, sessions

    s, sent = auth_service
    first = login(s, sent)
    before = s.get_channel(first["owner_id"], "email")

    def failed(*args):
        raise ServiceError(502, "smtp_rejected", "验证码发送失败")

    monkeypatch.setattr(s, "send_verification", failed)
    with pytest.raises(ServiceError):
        s.start_email_change(first["owner_id"], "new@example.org", limited=False)
    assert s.get_channel(first["owner_id"], "email") == before
    with pytest.raises(ServiceError):
        s.start_login("unknown@example.org", limited=False)
    with s.db.engine.connect() as conn:
        assert conn.execute(select(auth_challenges)).first() is None
        assert len(conn.execute(select(accounts)).all()) == 1
        stored = str(conn.execute(select(accounts)).all()) + str(
            conn.execute(select(sessions)).all()
        )
    assert "alice@example.org" not in stored
    assert first["session"] not in stored


def test_concurrent_confirmation_creates_one_account_and_one_session(auth_service):
    from ashare_lab.notification_models import accounts, sessions

    s, sent = auth_service
    s.start_login("alice@example.org", limited=False)
    code = sent[-1][1]

    def confirm(_):
        try:
            return s.login("alice@example.org", code)["owner_id"]
        except ServiceError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(confirm, range(2)))
    assert sum(value is not None for value in results) == 1
    with s.db.engine.connect() as conn:
        assert len(conn.execute(select(accounts)).all()) == 1
        assert len(conn.execute(select(sessions)).all()) == 1


def upgrade(db, version):
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).parents[1] / "src/ashare_lab/migrations")
    )
    with db.engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, version)


def test_0004_upgrade_preserves_legacy_subscription_and_invalidates_credentials(
    auth_service, tmp_path
):
    s, sent = auth_service
    db = Database(tmp_path / "legacy.db")
    upgrade(db, "0004")
    owner = identifier()
    with s.db.engine.connect() as source, db.engine.begin() as target:
        for table in (versions, heads):
            target.execute(
                table.insert(), [dict(row) for row in source.execute(select(table)).mappings()]
            )
    with db.engine.begin() as conn:
        conn.execute(
            owners.insert().values(
                id=owner,
                session_hash=digest("old-session"),
                recovery_hash=digest("old-recovery"),
                expires_at="2099-01-01",
            )
        )
    legacy = NotificationService(s.settings, db, s.repository)
    legacy._save_channel(owner, "email", "Alice@Example.org", True)
    legacy._save_channel(owner, "bark", "legacy-device", True)
    # Seed the old wire shape directly: new requests no longer accept Bark.
    sub = subscription(legacy, owner, ["email"])
    from ashare_lab.notification_models import subscriptions

    with db.engine.begin() as conn:
        conn.execute(
            subscriptions.update()
            .where(subscriptions.c.id == sub["id"])
            .values(channels=["bark", "email"])
        )
    before = legacy.get_channel(owner, "email")
    db.initialize()
    db.initialize()
    legacy.send_verification = s.send_verification
    result = login(legacy, sent)
    assert result["owner_id"] == owner
    assert legacy.list_subscriptions(owner)[0]["id"] == sub["id"]
    assert legacy.get_channel(owner, "email") == before
    assert not legacy.get_channel(owner, "bark")["verified"]
    assert legacy.list_subscriptions(owner)[0]["channels"] == ["email"]
    with pytest.raises(ServiceError):
        legacy.authenticate("old-session")
    with db.engine.connect() as conn:
        row = conn.execute(select(owners)).mappings().one()
        assert row["recovery_hash"] != digest("old-recovery")
        assert conn.execute(text("PRAGMA foreign_key_check")).all() == []


def test_api_login_cookie_logout_origin_and_removed_routes(tmp_path, monkeypatch):
    app = create_app(
        Settings(
            _env_file=None,
            database_path=tmp_path / "api.db",
            data_dir=tmp_path,
            notification_cookie_secure=False,
            notification_encryption_key=Fernet.generate_key().decode(),
            api_access_token="shared",
        )
    )
    sent = []
    monkeypatch.setattr(
        app.state.notifications, "send_verification", lambda address, code: sent.append(code)
    )
    client = TestClient(app)
    headers = {"origin": "http://testserver"}
    prefix = "/api/v1/notifications"
    assert (
        client.post(
            prefix + "/auth/email/verification", json={"address": "alice@example.org"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            prefix + "/auth/email/verification",
            json={"address": "alice@example.org"},
            headers={"origin": "https://evil.example"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            prefix + "/auth/email/verification",
            json={"address": "alice@example.org"},
            headers=headers,
        ).status_code
        == 200
    )
    response = client.post(
        prefix + "/auth/email/verify",
        json={"address": "alice@example.org", "code": sent[-1]},
        headers=headers,
    )
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "session" not in response.json() and "recovery_code" not in response.json()
    assert response.json()["email"] == "a***@example.org"
    assert client.get(prefix + "/session").status_code == 200
    assert client.get(prefix + "/subscriptions").status_code == 200
    assert (
        client.post(prefix + "/recover", json={"recovery_code": "old"}, headers=headers).status_code
        == 401
    )
    assert client.post(prefix + "/session", headers=headers).status_code == 401
    assert client.delete(prefix + "/session", headers=headers).status_code == 200
    assert client.get(prefix + "/session").status_code == 401
    assert client.get("/api/v1/backtest/options").status_code == 401


def test_migration_interruption_rolls_back_and_can_resume(tmp_path):
    db = Database(tmp_path / "interrupted.db")
    upgrade(db, "0004")
    owner = identifier()
    with db.engine.begin() as conn:
        conn.execute(
            owners.insert().values(
                id=owner,
                session_hash=digest("old-session"),
                recovery_hash=digest("old-recovery"),
                expires_at="2099-01-01",
            )
        )

    def interrupted(conn, cursor, statement, parameters, context, executemany):
        if "CREATE TABLE notification_sessions" in statement:
            raise RuntimeError("simulated migration interruption")

    event.listen(db.engine, "before_cursor_execute", interrupted)
    try:
        with pytest.raises(RuntimeError, match="simulated migration interruption"):
            db.initialize()
    finally:
        event.remove(db.engine, "before_cursor_execute", interrupted)
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0004"
        assert conn.execute(select(owners.c.session_hash)).scalar() == digest("old-session")
        assert (
            conn.execute(
                text("SELECT name FROM sqlite_master WHERE name='notification_accounts'")
            ).first()
            is None
        )
    db.initialize()
    db.initialize()
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0009"
        assert conn.execute(text("PRAGMA foreign_key_check")).all() == []


def test_legacy_duplicate_emails_refuse_automatic_merge(auth_service):
    from ashare_lab.notification_models import accounts

    s, sent = auth_service
    for address in ("alice@example.org", "Alice@Example.org"):
        owner = identifier()
        with s.db.engine.begin() as conn:
            conn.execute(
                owners.insert().values(
                    id=owner,
                    session_hash=f"disabled-session:{owner}",
                    recovery_hash=f"disabled-recovery:{owner}",
                    expires_at="1970-01-01",
                )
            )
        s._save_channel(owner, "email", address, True)
        subscription(s, owner)
    with pytest.raises(ServiceError) as error:
        login(s, sent)
    assert error.value.code == "legacy_email_conflict"
    with s.db.engine.connect() as conn:
        assert conn.execute(select(accounts)).first() is None


def test_session_expiration_and_target_limits_survive_service_restart(auth_service):
    from ashare_lab.notification_models import sessions

    s, sent = auth_service
    first = login(s, sent)
    s.start_login("limited@example.org")
    restarted = NotificationService(s.settings, s.db, s.repository)
    restarted.send_verification = s.send_verification
    with pytest.raises(ServiceError) as error:
        restarted.start_login("limited@example.org")
    assert error.value.code == "rate_limited"
    with s.db.engine.begin() as conn:
        conn.execute(sessions.update().values(expires_at="2000-01-01"))
    with pytest.raises(ServiceError) as error:
        restarted.authenticate(first["session"])
    assert error.value.status == 401


def test_unconfigured_smtp_cannot_create_account(auth_service, monkeypatch):
    from ashare_lab.notification_models import accounts, auth_challenges

    s, sent = auth_service
    # Restore the real adapter entrypoint: no fixture may pretend SMTP worked.
    monkeypatch.setattr(s, "send_verification", NotificationService.send_verification.__get__(s))
    with pytest.raises(ServiceError) as error:
        s.start_login("alice@example.org", limited=False)
    assert error.value.code == "email_unavailable"
    with s.db.engine.connect() as conn:
        assert conn.execute(select(accounts)).first() is None
        assert conn.execute(select(auth_challenges)).first() is None


def test_manual_sample_migration_rollback_and_old_history(tmp_path):
    db = Database(tmp_path / "samples.db")
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).parents[1] / "src/ashare_lab/migrations")
    )
    with db.engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "0006")
        conn.execute(
            owners.insert().values(
                id="legacy", session_hash="s", recovery_hash="r", expires_at="2099"
            )
        )

    def interrupt(connection, cursor, statement, parameters, context, executemany):
        if "CREATE TABLE notification_test_deliveries" in statement:
            raise RuntimeError("simulated migration interruption")

    event.listen(db.engine, "before_cursor_execute", interrupt)
    try:
        with pytest.raises(RuntimeError):
            db.initialize()
    finally:
        event.remove(db.engine, "before_cursor_execute", interrupt)
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0006"
        assert (
            conn.execute(
                text("SELECT name FROM sqlite_master WHERE name='notification_test_requests'")
            ).first()
            is None
        )
        assert conn.execute(select(owners.c.id)).scalar() == "legacy"
    db.initialize()
    db.initialize()
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0009"
        assert conn.execute(select(owners.c.id)).scalar() == "legacy"
        assert conn.execute(text("PRAGMA foreign_key_check")).all() == []
