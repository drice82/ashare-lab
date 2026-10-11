"""Manually checkable V1 examples: crossed prices, isolated owners, durable fan-out."""

from datetime import UTC, datetime

import pytest
from ashare_lab.api import create_app
from ashare_lab.config import Settings
from ashare_lab.db import identifier
from ashare_lab.notification_models import channels, deliveries
from ashare_lab.notifications import NotificationService, SubscriptionRequest
from ashare_lab.schemas import StrategyConfig
from ashare_lab.services import ServiceError
from ashare_lab.signals import signal, warmup
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select


@pytest.fixture
def notification_service(repository):
    return NotificationService(
        Settings(
            _env_file=None,
            database_path=repository.db.engine.url.database,
            data_dir=repository.root,
            notification_encryption_key=Fernet.generate_key().decode(),
            notification_cookie_secure=False,
        ),
        repository.db,
        repository,
    )


def test_cross_and_repeated_breakout():
    ma = StrategyConfig(id="ma_cross", short_window=2, long_window=3)
    assert signal(ma, [3, 2, 1, 4], [], []) == "buy"
    assert signal(ma, [1, 2, 3, 0.5], [], []) == "sell"
    assert signal(ma, [1, 2, 3, 4], [], []) is None
    config = StrategyConfig(id="donchian_breakout", entry_window=2, exit_window=2)
    assert signal(config, [1, 2, 3], [1, 2, 3], [1, 2, 3]) == "buy"
    assert signal(config, [2, 3, 4], [2, 3, 4], [2, 3, 4]) == "buy"
    assert warmup(StrategyConfig(id="macd_cross")) == 139
    assert warmup(StrategyConfig(id="rsi_reversal")) == 71


def signed_session(service):
    """Use actual code verification; the isolated transport captures the generated code."""
    sent = []
    original = service.send_verification
    service.send_verification = lambda address, code: sent.append(code)
    address = f"test-{identifier()}@example.org"
    try:
        service.start_login(address, limited=False)
        return service.login(address, sent[-1])
    finally:
        service.send_verification = original


def test_atomic_fanout_dedup_cancel_and_unsubscribe(notification_service):
    s = notification_service
    owner = signed_session(s)["owner_id"]
    for kind in ("webhook", "email"):
        s._save_channel(owner, kind, "example@example.org" if kind == "email" else "device", True)
    request = SubscriptionRequest(
        stock_code="000001.SZ",
        strategy=StrategyConfig(id="ma_cross"),
        channels=["webhook", "email"],
    )
    sub = s.save_subscription(owner, request)
    assert s.save_subscription(owner, request)["id"] == sub["id"]
    outcome = {
        "status": "checked",
        "direction": "buy",
        "reason": "短均线上穿长均线",
        "data_versions": {},
        "strategy_version": sub["strategy_version"],
    }
    s.record_check(sub, "2026-10-09", outcome)
    s.record_check(sub, "2026-10-09", outcome)
    assert len(s.list_deliveries(owner)["items"]) == 2
    token = s.unsubscribe_token(sub, s.get_channel(owner, "email"))
    s.unsubscribe(token)
    updated = s.list_subscriptions(owner)[0]
    assert updated["channels"] == ["webhook"]
    rows = s.list_deliveries(owner)["items"]
    assert {r["channel"]: r["status"] for r in rows} == {"webhook": "pending", "email": "cancelled"}
    s.patch_subscription(owner, sub["id"], {"status": "paused"})
    assert all(r["status"] == "cancelled" for r in s.list_deliveries(owner)["items"])
    with pytest.raises(ServiceError):
        s.patch_subscription(signed_session(s)["owner_id"], sub["id"], {"status": "active"})


def test_verification_resend_attempt_limit_and_secrets(notification_service, monkeypatch):
    s = notification_service
    owner = signed_session(s)["owner_id"]
    sent = []
    monkeypatch.setattr(s, "send_verification", lambda address, code: sent.append(code))
    s.start_email_change(owner, "test@example.org", limited=False)
    old = sent[-1]
    s.start_email_change(owner, "test@example.org", limited=False)
    with pytest.raises(ServiceError):
        s.change_email(owner, "test@example.org", old)
    s.change_email(owner, "test@example.org", sent[-1])
    with s.db.engine.connect() as c:
        row = c.execute(select(channels)).mappings().one()
    assert "test@example.org" not in str(row)
    s.start_email_change(owner, "new@example.org", limited=False)
    for _ in range(5):
        with pytest.raises(ServiceError):
            s.change_email(owner, "new@example.org", "invalid")
    with pytest.raises(ServiceError):
        s.change_email(owner, "new@example.org", sent[-1])


def test_public_routes_and_origin(tmp_path):
    client = TestClient(
        create_app(
            Settings(
                _env_file=None,
                database_path=tmp_path / "db",
                data_dir=tmp_path,
                api_access_token="shared",
                notification_cookie_secure=False,
                notification_encryption_key=Fernet.generate_key().decode(),
            )
        )
    )
    assert client.get("/api/v1/notifications/options").status_code == 200
    assert (
        client.get("/api/v1/notifications/subscriptions").json()["code"]
        == "notification_unauthorized"
    )
    assert (
        client.post(
            "/api/v1/notifications/auth/email/verification",
            json={"address": "test@example.org"},
            headers={"origin": "https://evil.example"},
        ).status_code
        == 403
    )
    sent = []
    service = client.app.state.notifications
    service.send_verification = lambda address, code: sent.append(code)
    assert (
        client.post(
            "/api/v1/notifications/auth/email/verification",
            json={"address": "test@example.org"},
            headers={"origin": "http://testserver"},
        ).status_code
        == 200
    )
    response = client.post(
        "/api/v1/notifications/auth/email/verify",
        json={"address": "test@example.org", "code": sent[-1]},
        headers={"origin": "http://testserver"},
    )
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "session" not in response.json()
    assert client.get("/api/v1/notifications/subscriptions").status_code == 200
    assert client.get("/api/v1/notifications/not-a-route").status_code == 401
    assert client.get("/api/v1/backtest/options").status_code == 401


@pytest.mark.parametrize(
    "strategy,prices,expected",
    [
        (
            {"id": "macd_cross", "fast_window": 2, "slow_window": 3, "signal_window": 2},
            [20 - i for i in range(17)] + [20],
            "buy",
        ),
        (
            {"id": "macd_cross", "fast_window": 2, "slow_window": 3, "signal_window": 2},
            [i + 1 for i in range(17)] + [1],
            "sell",
        ),
        ({"id": "rsi_reversal", "period": 2}, [20 - i for i in range(11)] + [20], "buy"),
        ({"id": "rsi_reversal", "period": 2}, [i + 1 for i in range(11)] + [1], "sell"),
        ({"id": "bollinger_reversion", "period": 3, "deviations": 0.5}, [10, 10, 5, 6], "buy"),
        ({"id": "bollinger_reversion", "period": 3}, [3, 3, 2, 4], "sell"),
        ({"id": "donchian_breakout", "entry_window": 2, "exit_window": 2}, [3, 2, 1], "sell"),
    ],
)
def test_all_strategy_examples(strategy, prices, expected):
    config = StrategyConfig.model_validate(strategy)
    assert signal(config, prices, prices, prices) == expected
    flat = [10.0] * len(prices)
    assert signal(config, flat, flat, flat) is None


def test_bounded_signal_and_insufficient_suspend():
    import polars as pl
    from ashare_lab.signals import calculate_signal

    days = ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"]

    class Repository:
        def __init__(self):
            self.prices = [3, 2, 1, 4]
            self.suspended = False
            self.complete = True
            self.reads = []

        def read(self, dataset, snapshot, codes=None, start_date=None, end_date=None):
            self.reads.append((dataset, start_date, end_date))
            if dataset != "stock_basic":
                assert end_date == days[-1]
            if dataset == "stock_basic":
                return pl.DataFrame([{"list_status": "L", "list_date": "2000-01-01"}])
            if dataset == "trade_cal":
                return pl.DataFrame(
                    [{"cal_date": day, "is_open": 1, "exchange": "SSE"} for day in days]
                )
            if dataset == "daily":
                return pl.DataFrame(
                    [
                        {"trade_date": day, "close": price, "volume": 100}
                        for day, price in zip(days, self.prices, strict=True)
                    ]
                )
            if dataset == "adj_factor":
                return pl.DataFrame([{"trade_date": day, "adj_factor": 1} for day in days])
            return (
                pl.DataFrame([{"trade_date": days[-1], "suspend_type": "S"}])
                if self.suspended
                else pl.DataFrame()
            )

        def coverage(self, dataset, snapshot):
            return {"all": {"complete": self.complete, "trade_dates": days}}

    repo = Repository()
    config = StrategyConfig(id="ma_cross", short_window=2, long_window=3)
    versions = {
        name: "pinned" for name in ["stock_basic", "daily", "adj_factor", "suspend_d", "trade_cal"]
    }
    result = calculate_signal(repo, "000001.SZ", config, days[-1], versions, "1")
    assert result["direction"] == "buy"
    repo.suspended = True
    assert calculate_signal(repo, "000001.SZ", config, days[-1], versions, "1")["direction"] is None
    repo.complete = False
    assert (
        calculate_signal(repo, "000001.SZ", config, days[-1], versions, "1")["status"]
        == "insufficient_data"
    )
    repo.complete, repo.suspended, repo.prices = True, False, [3, 2, 0, 4]
    assert (
        calculate_signal(repo, "000001.SZ", config, days[-1], versions, "1")["status"]
        == "insufficient_data"
    )


def test_expiry_and_old_unsubscribe_invalidated(notification_service, monkeypatch):
    from ashare_lab.notification_models import auth_challenges

    s = notification_service
    owner = signed_session(s)["owner_id"]
    s._save_channel(owner, "email", "old@example.org", True)
    sub = s.save_subscription(
        owner,
        SubscriptionRequest(
            stock_code="000001.SZ", strategy=StrategyConfig(id="ma_cross"), channels=["email"]
        ),
    )
    token = s.unsubscribe_token(sub, s.get_channel(owner, "email"))
    s._save_channel(owner, "email", "new@example.org", True)
    with pytest.raises(ServiceError):
        s.unsubscribe(token)
    sent = []
    monkeypatch.setattr(s, "send_verification", lambda address, code: sent.append(code))
    s.start_email_change(owner, "new@example.org", limited=False)
    with s.db.engine.begin() as conn:
        conn.execute(auth_challenges.update().values(expires_at="2000-01-01"))
    with pytest.raises(ServiceError):
        s.change_email(owner, "new@example.org", sent[-1])


def test_independent_retry_recovery_and_stable_message_id(notification_service, monkeypatch):
    from ashare_lab.db import versions
    from ashare_lab.notification_transport import DeliveryError

    s = notification_service
    s.settings.notification_auto_send = True
    with s.db.engine.begin() as conn:
        conn.execute(versions.update().values(source="real"))
    monkeypatch.setattr(s, "latest_closed_day", lambda *args: "2026-10-09")
    owner = signed_session(s)["owner_id"]
    for kind in ("webhook", "email"):
        s._save_channel(owner, kind, "test@example.org" if kind == "email" else "device", True)
    sub = s.save_subscription(
        owner,
        SubscriptionRequest(
            stock_code="000001.SZ",
            strategy=StrategyConfig(id="ma_cross"),
            channels=["webhook", "email"],
        ),
    )
    s.record_check(
        sub,
        "2026-10-09",
        {
            "status": "checked",
            "direction": "buy",
            "reason": "cross",
            "data_versions": {},
            "strategy_version": "1",
        },
    )
    ids = []

    def send(kind, target, title, body, delivery_id, url):
        ids.append(delivery_id)
        if kind == "webhook":
            raise DeliveryError("network_error", temporary=True)
        assert "/unsubscribe#" in url

    monkeypatch.setattr(s, "_send", send)
    assert s.send_once("webhook")
    assert s.send_once("email")
    rows = {r["channel"]: r for r in s.list_deliveries(owner)["items"]}
    assert rows["email"]["status"] == "accepted"
    assert rows["webhook"]["status"] == "pending"
    assert rows["webhook"]["attempts"] == 1
    with s.db.engine.begin() as conn:
        conn.execute(
            deliveries.update()
            .where(deliveries.c.channel == "webhook")
            .values(status="sending", next_attempt="2000-01-01")
        )
    s.recover_deliveries()
    assert s.send_once("webhook")
    assert ids[0] == ids[2]
    assert s.message_id(ids[0]) == s.message_id(ids[2])
    with s.db.engine.begin() as conn:
        conn.execute(
            deliveries.update()
            .where(deliveries.c.channel == "webhook")
            .values(next_attempt="2000-01-01")
        )
    s.patch_subscription(owner, sub["id"], {"status": "paused"})
    assert not s.send_once("webhook")


def test_atomic_rollback(notification_service, monkeypatch):
    s = notification_service
    owner = signed_session(s)["owner_id"]
    for kind in ("webhook", "email"):
        s._save_channel(owner, kind, "test@example.org" if kind == "email" else "device", True)
    sub = s.save_subscription(
        owner,
        SubscriptionRequest(
            stock_code="000001.SZ",
            strategy=StrategyConfig(id="ma_cross"),
            channels=["webhook", "email"],
        ),
    )
    original = s.get_channel

    def crash(owner, kind, conn=None):
        if kind == "email":
            raise RuntimeError("simulated crash before commit")
        return original(owner, kind, conn)

    monkeypatch.setattr(s, "get_channel", crash)
    with pytest.raises(RuntimeError):
        s.record_check(
            sub, "2026-10-09", {"status": "checked", "direction": "buy", "reason": "cross"}
        )
    assert s.list_deliveries(owner)["items"] == []
    from ashare_lab.notification_models import checks

    with s.db.engine.connect() as c:
        assert c.execute(select(checks)).first() is None


def test_schedule_dedup_wait_recovery_and_pinned_versions(notification_service, monkeypatch):
    """Two owners share one compute task; a restart uses old pinned data after publication."""
    import polars as pl
    from ashare_lab.db import jobs, versions
    from ashare_lab.notification_models import subscriptions
    from ashare_lab.signals import calculate_signal
    from ashare_lab.worker import Worker

    s = notification_service
    s.settings.notification_auto_send = True
    owners_created = []
    for _ in range(2):
        owner = signed_session(s)["owner_id"]
        owners_created.append(owner)
        s._save_channel(owner, "webhook", "test-device", True)
        s.save_subscription(
            owner,
            SubscriptionRequest(
                stock_code="000001.SZ",
                strategy=StrategyConfig(id="ma_cross", short_window=2, long_window=3),
                channels=["webhook"],
            ),
        )
    with s.db.engine.begin() as conn:
        conn.execute(versions.update().values(source="real"))
        conn.execute(subscriptions.update().values(effective_after="2024-06-27"))
    at = datetime(2024, 6, 28, 9, tzinfo=UTC)  # Shanghai 17:00
    snapshot = s.db.snapshot()
    s.schedule(at)
    s.schedule(at)
    with s.db.engine.connect() as conn:
        tasks = (
            conn.execute(select(jobs).where(jobs.c.kind == "notification_signal")).mappings().all()
        )
    assert len(tasks) == 1
    job = dict(tasks[0])
    expected = calculate_signal(
        s.repository,
        "000001.SZ",
        StrategyConfig(id="ma_cross", short_window=2, long_window=3),
        "2024-06-28",
        {k: v for k, v in job["snapshot"].items() if not k.startswith("_")},
        job["payload"]["strategy_version"],
    )
    frame = s.repository.read("daily", snapshot).with_columns(pl.lit(999.0).alias("close"))
    s.repository.publish("daily", frame, "new-daily", "real", "new-daily")
    s.db.update(job["id"], status="running")
    worker = Worker(s.settings)
    monkeypatch.setattr(worker.notifications, "latest_closed_day", lambda *args: "2024-06-28")
    worker.recover()
    assert worker.run_once()
    assert s.db.job(job["id"])["result"] == expected
    old_result = s.db.job(job["id"])["result"]
    worker.recover()
    assert s.db.job(job["id"])["result"] == old_result
    assert all(
        sub["last_check"] == "2024-06-28"
        for sub in [r for owner in owners_created for r in s.list_subscriptions(owner)]
    )
    # An insufficient outcome does not seal a completed check; next-minute recheck
    # uses a new durable job, rather than falling back to an older trading day.
    with s.db.engine.begin() as conn:
        from ashare_lab.notification_models import checks

        conn.execute(deliveries.delete())
        conn.execute(checks.delete())
        conn.execute(jobs.update().values(status="partial"))
    monkeypatch.setattr(s, "latest_closed_day", lambda *args: "2024-06-28")
    s.schedule(at)
    with s.db.engine.connect() as conn:
        assert conn.execute(select(jobs).where(jobs.c.status == "queued")).first() is not None


def test_calendar_stale_no_fallback_and_fixture_disabled(notification_service):
    s = notification_service
    s.settings.notification_auto_send = True
    assert s.latest_closed_day(s.db.snapshot(), datetime(2026, 10, 9, 9, tzinfo=UTC)) is None
    assert (
        s.latest_closed_day(s.db.snapshot(), datetime(2024, 6, 28, 9, tzinfo=UTC)) == "2024-06-28"
    )
    s.schedule(datetime(2024, 6, 28, 9, tzinfo=UTC))
    from ashare_lab.db import jobs

    with s.db.engine.connect() as conn:
        assert (
            conn.execute(select(jobs).where(jobs.c.kind == "notification_signal")).first() is None
        )


def test_retry_exhaustion_expiry_and_permanent_failure(notification_service, monkeypatch):
    from ashare_lab.db import versions
    from ashare_lab.notification_transport import DeliveryError

    s = notification_service
    s.settings.notification_auto_send = True
    with s.db.engine.begin() as conn:
        conn.execute(versions.update().values(source="real"))
    monkeypatch.setattr(s, "latest_closed_day", lambda *args: "2026-10-09")
    owner = signed_session(s)["owner_id"]
    s._save_channel(owner, "webhook", "test-device", True)
    sub = s.save_subscription(
        owner,
        SubscriptionRequest(
            stock_code="000001.SZ", strategy=StrategyConfig(id="ma_cross"), channels=["webhook"]
        ),
    )
    outcome = {
        "status": "checked",
        "direction": "buy",
        "reason": "cross",
        "data_versions": {},
        "strategy_version": "1",
    }
    s.record_check(sub, "2026-10-09", outcome)

    def temporary(*args):
        raise DeliveryError("network_error", temporary=True)

    monkeypatch.setattr(s, "_send", temporary)
    for _ in range(5):
        with s.db.engine.begin() as conn:
            conn.execute(deliveries.update().values(next_attempt="2000-01-01"))
        assert s.send_once("webhook")
    row = s.list_deliveries(owner)["items"][0]
    assert (row["status"], row["attempts"]) == ("failed", 5)
    assert not s.send_once("webhook")
    s.record_check(sub, "2026-10-08", outcome)
    assert s.send_once("webhook")
    assert any(r["status"] == "expired" for r in s.list_deliveries(owner)["items"])
    s.record_check(sub, "2026-10-10", outcome)

    def permanent(*args):
        raise DeliveryError("webhook_rejected", disable_channel=True)

    monkeypatch.setattr(s, "_send", permanent)
    assert s.send_once("webhook")
    assert s.get_channel(owner, "webhook")["status"] == "disabled"
    assert any(
        r["error"] == "webhook_rejected" and r["status"] == "failed"
        for r in s.list_deliveries(owner)["items"]
    )


def test_rate_limits_and_ownership_api(tmp_path):
    client = TestClient(
        create_app(
            Settings(
                _env_file=None,
                database_path=tmp_path / "db",
                data_dir=tmp_path,
                notification_cookie_secure=False,
                notification_encryption_key=Fernet.generate_key().decode(),
            )
        )
    )
    route = "/api/v1/notifications/auth/email/verify"
    payload = {"address": "test@example.org", "code": "000000"}
    assert client.post(route, json=payload).status_code == 403
    for _ in range(10):
        assert (
            client.post(route, json=payload, headers={"origin": "http://testserver"}).status_code
            == 422
        )
    assert (
        client.post(route, json=payload, headers={"origin": "http://testserver"}).status_code == 429
    )


def test_channel_change_does_not_delay_unrelated_subscription(notification_service):
    s = notification_service
    owner = signed_session(s)["owner_id"]
    s._save_channel(owner, "webhook", "test-device", True)
    sub = s.save_subscription(
        owner,
        SubscriptionRequest(
            stock_code="000001.SZ", strategy=StrategyConfig(id="ma_cross"), channels=["webhook"]
        ),
    )
    from ashare_lab.notification_models import subscriptions

    with s.db.engine.begin() as conn:
        conn.execute(subscriptions.update().values(effective_after="2024-01-01"))
    s._save_channel(owner, "email", "test@example.org", True)
    assert s.list_subscriptions(owner)[0]["effective_after"] == "2024-01-01"
    s.record_check(sub, "2026-10-09", {"status": "checked", "direction": "buy", "reason": "cross"})
    s._save_channel(owner, "webhook", "replacement-device", True)
    assert s.list_deliveries(owner)["items"][0]["status"] == "cancelled"
    assert s.list_subscriptions(owner)[0]["effective_after"] != "2024-01-01"


def test_smtp_configuration_state_and_subscription_limit(notification_service):
    s = notification_service
    s.settings.smtp_host = "smtp.fixture"
    s.settings.smtp_from_email = "sender@example.org"
    assert s.smtp_available()
    s._smtp_error("smtp_configuration_error")
    assert not s.smtp_available()
    s.settings.smtp_username = "corrected-user"
    assert s.smtp_available()
    s.settings.notification_subscription_limit = 2
    owner = signed_session(s)["owner_id"]
    s._save_channel(owner, "webhook", "test-device", True)
    for direction in (["buy"], ["sell"]):
        s.save_subscription(
            owner,
            SubscriptionRequest(
                stock_code="000001.SZ",
                strategy=StrategyConfig(id="ma_cross"),
                channels=["webhook"],
                directions=direction,
            ),
        )
    with pytest.raises(ServiceError) as error:
        s.save_subscription(
            owner,
            SubscriptionRequest(
                stock_code="000001.SZ", strategy=StrategyConfig(id="ma_cross"), channels=["webhook"]
            ),
        )
    assert error.value.code == "subscription_limit"


def test_sensitive_validation_input_not_echoed(tmp_path):
    client = TestClient(
        create_app(
            Settings(
                _env_file=None,
                database_path=tmp_path / "db",
                data_dir=tmp_path,
                notification_cookie_secure=False,
                notification_encryption_key=Fernet.generate_key().decode(),
            )
        )
    )
    result = client.post(
        "/api/v1/notifications/auth/email/verify",
        json={"address": "test@example.org", "code": "synthetic-credential" * 50},
        headers={"origin": "http://testserver"},
    )
    assert result.status_code == 422
    assert "synthetic-credential" not in result.text


def test_old_target_failure_does_not_cancel_replacement(notification_service):
    from ashare_lab.notification_transport import DeliveryError

    s = notification_service
    owner = signed_session(s)["owner_id"]
    s._save_channel(owner, "webhook", "old-device", True)
    old_revision = s.get_channel(owner, "webhook")["revision"]
    sub = s.save_subscription(
        owner,
        SubscriptionRequest(
            stock_code="000001.SZ", strategy=StrategyConfig(id="ma_cross"), channels=["webhook"]
        ),
    )
    s._save_channel(owner, "webhook", "new-device", True)
    s.record_check(sub, "2026-10-09", {"status": "checked", "direction": "buy", "reason": "cross"})
    s._transport_failure(
        owner, "webhook", old_revision, DeliveryError("webhook_rejected", disable_channel=True)
    )
    assert s.get_channel(owner, "webhook")["status"] == "active"
    assert s.list_deliveries(owner)["items"][0]["status"] == "pending"


def test_message_id_survives_sender_configuration_change(notification_service):
    s = notification_service
    first = s.message_id("delivery-id")
    s.settings.smtp_from_email = "new-sender@new-domain.example"
    assert s.message_id("delivery-id") == first


def test_subscription_names_use_one_snapshot_without_rewriting_history(
    notification_service, monkeypatch
):
    s = notification_service
    owner = signed_session(s)["owner_id"]
    sub = s.save_subscription(
        owner,
        SubscriptionRequest(
            stock_code="000001.SZ", strategy=StrategyConfig(id="ma_cross"), channels=["email"]
        ),
    )
    s.record_check(
        sub, "2026-10-09", {"status": "checked", "direction": "buy", "stock_name": "历史名称"}
    )
    original = s.repository.read
    reads = []

    def read(dataset, snapshot, codes=None, *args, **kwargs):
        reads.append((dataset, snapshot, codes))
        return original(dataset, snapshot, codes, *args, **kwargs)

    monkeypatch.setattr(s.repository, "read", read)
    listed = s.list_subscriptions(owner)
    assert listed[0]["stock_name"] == "测试证券1"
    assert len(reads) == 1 and reads[0][0] == "stock_basic" and reads[0][2] == ["000001.SZ"]
    assert s.list_deliveries(owner)["items"][0]["outcome"]["stock_name"] == "历史名称"


def test_webhook_binding_test_failure_cancels_old_target_and_masks_url(
    notification_service, monkeypatch
):
    from ashare_lab import notifications
    from ashare_lab.notification_transport import DeliveryError

    s = notification_service
    owner = signed_session(s)["owner_id"]
    target = "https://receiver.example.org/synthetic-key"
    monkeypatch.setattr(notifications, "validate_webhook_url", lambda url: None)
    monkeypatch.setattr(notifications, "send_webhook", lambda *args: None)
    s.bind_channel(owner, "webhook", target)
    assert s.get_channel(owner, "webhook")["verified"]
    assert target not in str(s.list_channels(owner))
    sub = s.save_subscription(
        owner,
        SubscriptionRequest(
            stock_code="000001.SZ", strategy=StrategyConfig(id="ma_cross"), channels=["webhook"]
        ),
    )
    s.record_check(sub, "2026-10-09", {"status": "checked", "direction": "buy"})

    def reject(*args):
        raise DeliveryError("webhook_rejected", disable_channel=True)

    monkeypatch.setattr(notifications, "send_webhook", reject)
    with pytest.raises(ServiceError) as error:
        s.bind_channel(owner, "webhook", "https://receiver.example.org/replacement-key")
    assert error.value.code == "webhook_rejected"
    assert not s.get_channel(owner, "webhook")["verified"]
    assert s.list_deliveries(owner)["items"][0]["status"] == "cancelled"


def test_subscription_sample_isolated_and_partial_failure(notification_service, monkeypatch):
    from ashare_lab.notification_models import checks, test_deliveries
    from ashare_lab.notification_transport import DeliveryError

    s = notification_service
    owner = signed_session(s)["owner_id"]
    s._save_channel(owner, "webhook", "https://example.org/push", True)
    sub = s.save_subscription(
        owner,
        SubscriptionRequest(
            stock_code="000001.SZ",
            strategy=StrategyConfig(id="ma_cross"),
            channels=["email", "webhook"],
            directions=["buy", "sell"],
        ),
    )
    s.patch_subscription(owner, sub["id"], {"status": "paused"})
    before = s.list_subscriptions(owner)
    sent = []

    def send(kind, target, title, body, delivery_id, url=None):
        sent.append((kind, title, body))
        if kind == "webhook":
            raise DeliveryError("network_error")

    monkeypatch.setattr(s, "_send", send)
    result = s.test_subscription(owner, sub["id"], "sell")
    assert [r["status"] for r in result["results"]] == ["accepted", "failed"]
    assert len(sent) == 2
    assert all("测试" in title and "示例" in body and "卖出" in body for _, title, body in sent)
    assert s.list_subscriptions(owner) == before
    with s.db.engine.connect() as conn:
        assert conn.execute(select(checks)).first() is None
        assert conn.execute(select(deliveries)).first() is None
        assert len(conn.execute(select(test_deliveries)).all()) == 2
    history = s.list_deliveries(owner, "email", 1, 0)
    assert history["total"] == 1 and history["items"][0]["is_test"]
    with pytest.raises(ServiceError):
        s.test_subscription("another-owner", sub["id"], "buy")


def test_test_quota_burst_cooldown_and_restart(notification_service, monkeypatch):
    s = notification_service
    owner = signed_session(s)["owner_id"]
    clock = [10000.0]
    monkeypatch.setattr(s, "_test_timestamp", lambda: clock[0])
    monkeypatch.setattr(s, "_send", lambda *args: None)
    for _ in range(5):
        s.test_channel(owner, "email")
    with pytest.raises(ServiceError) as error:
        s.test_channel(owner, "email")
    assert error.value.status == 429
    restarted = NotificationService(s.settings, s.db, s.repository)
    monkeypatch.setattr(restarted, "_test_timestamp", lambda: clock[0])
    monkeypatch.setattr(restarted, "_send", lambda *args: None)
    with pytest.raises(ServiceError):
        restarted.test_channel(owner, "email")
    for _ in range(15):
        clock[0] += 60
        restarted.test_channel(owner, "email")
    clock[0] += 60
    with pytest.raises(ServiceError):
        restarted.test_channel(owner, "email")
    clock[0] += 3600
    restarted.test_channel(owner, "email")


def test_webhook_stop_enable_preserves_target(notification_service, monkeypatch):
    s = notification_service
    owner = signed_session(s)["owner_id"]
    s._save_channel(owner, "webhook", "https://example.org/push", True)
    old = s.get_channel(owner, "webhook")
    s.delete_channel(owner, "webhook")
    disabled = s.get_channel(owner, "webhook")
    assert disabled["ciphertext"] == old["ciphertext"]
    assert disabled["status"] == "disabled"
    sent = []
    monkeypatch.setattr(s, "_send", lambda *args: sent.append(args))
    s.enable_channel(owner, "webhook")
    assert sent[0][3] == "这是一条测试通知"
    assert s.get_channel(owner, "webhook")["status"] == "active"


def test_sample_validation_does_not_spend_quota_and_shared_failures_do(
    notification_service, monkeypatch
):
    from ashare_lab.notification_models import test_requests
    from ashare_lab.notification_transport import DeliveryError

    s = notification_service
    owner = signed_session(s)["owner_id"]
    sub = s.save_subscription(
        owner,
        SubscriptionRequest(
            stock_code="000001.SZ",
            strategy=StrategyConfig(id="ma_cross"),
            channels=["email"],
            directions=["buy"],
        ),
    )
    with pytest.raises(ServiceError):
        s.test_subscription(owner, sub["id"], "sell")
    with s.db.engine.connect() as conn:
        assert conn.execute(select(test_requests)).first() is None
    monkeypatch.setattr(s, "_test_timestamp", lambda: 10000.0)

    def send(*args):
        raise DeliveryError("network_error")

    monkeypatch.setattr(s, "_send", send)
    for _ in range(4):
        assert s.test_subscription(owner, sub["id"], "buy")["results"][0]["status"] == "failed"
    with pytest.raises(ServiceError) as error:
        s.test_channel(owner, "email")
    assert error.value.status == 502
    with pytest.raises(ServiceError) as error:
        s.test_subscription(owner, sub["id"], "buy")
    assert error.value.status == 429
    with s.db.engine.connect() as conn:
        assert len(conn.execute(select(test_requests)).all()) == 5


def test_quota_concurrent_reservation_is_atomic(notification_service, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    s = notification_service
    owner = signed_session(s)["owner_id"]
    monkeypatch.setattr(s, "_test_timestamp", lambda: 10000.0)

    def attempt(_):
        try:
            s.reserve_test(owner)
            return True
        except ServiceError as exc:
            assert exc.status == 429
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(10))) == 5


def test_interrupted_sample_is_unknown_and_migration_preserves_history(
    notification_service, monkeypatch
):
    s = notification_service
    owner = signed_session(s)["owner_id"]
    sub = s.save_subscription(
        owner,
        SubscriptionRequest(
            stock_code="000001.SZ",
            strategy=StrategyConfig(id="ma_cross"),
            channels=["email"],
        ),
    )
    outcome = {
        "direction": "buy",
        "status": "checked",
        "reason": "真实历史依据",
        "data_versions": {},
        "strategy_version": sub["strategy_version"],
    }
    s.record_check(sub, "2026-10-09", outcome)

    def interrupt(*args):
        raise KeyboardInterrupt()

    monkeypatch.setattr(s, "_send", interrupt)
    with pytest.raises(KeyboardInterrupt):
        s.test_subscription(owner, sub["id"], "buy")
    s.db.initialize()
    s.db.initialize()
    history = s.list_deliveries(owner)
    assert history["total"] == 2
    sample = next(row for row in history["items"] if row["is_test"])
    assert sample["status"] == "unknown"
    assert next(row for row in history["items"] if not row["is_test"])["outcome"] == outcome
    assert s.list_deliveries(owner, limit=1, offset=1)["total"] == 2
    assert len(s.list_deliveries(owner, limit=1, offset=1)["items"]) == 1


def test_new_notification_routes_authenticated_and_csrf_checked(notification_service, monkeypatch):
    from ashare_lab.notification_api import COOKIE, is_notification_route

    s = notification_service
    session = signed_session(s)
    sub = s.save_subscription(
        session["owner_id"],
        SubscriptionRequest(
            stock_code="000001.SZ",
            strategy=StrategyConfig(id="ma_cross"),
            channels=["email"],
        ),
    )
    app = create_app(s.settings)
    # Router closes over the app service; both services use the same persisted account.
    monkeypatch.setattr(NotificationService, "_send", lambda *args: None)
    client = TestClient(app)
    route = f"/api/v1/notifications/subscriptions/{sub['id']}/test"
    assert is_notification_route(route, "POST")
    assert is_notification_route("/api/v1/notifications/channels/webhook/enable", "POST")
    assert not is_notification_route(route, "GET")
    assert client.post(route, json={"direction": "buy"}).status_code == 403
    origin = {"origin": "http://testserver"}
    assert client.post(route, json={"direction": "buy"}, headers=origin).status_code == 401
    client.cookies.set(COOKIE, session["session"])
    assert client.post(route, json={"direction": "buy"}, headers=origin).status_code == 200
    assert client.post(route, json={"direction": "invalid"}, headers=origin).status_code == 422


def test_webhook_enable_failure_stays_disabled_and_repeat_enable_is_noop(
    notification_service, monkeypatch
):
    from ashare_lab.notification_models import test_requests
    from ashare_lab.notification_transport import DeliveryError

    s = notification_service
    owner = signed_session(s)["owner_id"]
    s._save_channel(owner, "webhook", "https://example.org/push", True)
    s.delete_channel(owner, "webhook")
    before = s.get_channel(owner, "webhook")

    def failure(*args):
        raise DeliveryError("network_error")

    monkeypatch.setattr(s, "_send", failure)
    with pytest.raises(ServiceError):
        s.enable_channel(owner, "webhook")
    assert s.get_channel(owner, "webhook") == before
    monkeypatch.setattr(s, "_send", lambda *args: None)
    s.enable_channel(owner, "webhook")
    active = s.get_channel(owner, "webhook")
    s.enable_channel(owner, "webhook")
    assert s.get_channel(owner, "webhook") == active
    with s.db.engine.connect() as conn:
        assert len(conn.execute(select(test_requests)).all()) == 2


def test_unavailable_sample_channels_skipped_and_empty_rejected(notification_service, monkeypatch):
    from ashare_lab.notification_models import test_requests

    s = notification_service
    owner = signed_session(s)["owner_id"]
    s._save_channel(owner, "webhook", "https://example.org/push", True)
    sub = s.save_subscription(
        owner,
        SubscriptionRequest(
            stock_code="000001.SZ",
            strategy=StrategyConfig(id="ma_cross"),
            channels=["email", "webhook"],
        ),
    )
    s.delete_channel(owner, "webhook")
    sent = []
    monkeypatch.setattr(s, "_send", lambda *args: sent.append(args))
    results = s.test_subscription(owner, sub["id"], "buy")["results"]
    assert len(sent) == 1 and sent[0][0] == "email"
    assert next(row for row in results if row["channel"] == "webhook")["status"] == "skipped"
    s.delete_channel(owner, "email")
    with pytest.raises(ServiceError) as error:
        s.test_subscription(owner, sub["id"], "buy")
    assert error.value.status == 409
    with s.db.engine.connect() as conn:
        assert len(conn.execute(select(test_requests)).all()) == 1
