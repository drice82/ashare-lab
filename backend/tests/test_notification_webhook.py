"""Checkable webhook examples: public targets, real POST shape, and legacy retirement."""

import json
import socket
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from ashare_lab.db import Database, identifier
from ashare_lab.notification_models import channels, deliveries, owners, subscriptions
from ashare_lab.notification_transport import DeliveryError, send_webhook, validate_webhook_url
from sqlalchemy import event, select, text


@pytest.fixture
def public_dns(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))
        ],
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://example.org/push",
        "https://user:password@example.org/push",
        "https://example.org/push#secret",
        "https://127.0.0.1/push",
        "https://[::1]/push",
        "https://10.0.0.1/push",
        "https://169.254.169.254/latest",
        "https://100.64.0.1/push",
        "https://[::ffff:127.0.0.1]/push",
        "https://example.org:99999/push",
        "https://example.org:0/push",
        "https://[fec0::1]/push",
        "https://[2002:7f00:1::]/push",
        "https://example.org/\r\nHost: localhost",
        "https://example.org/#",
    ],
)
def test_invalid_webhook_target(url, public_dns):
    with pytest.raises(DeliveryError) as caught:
        validate_webhook_url(url)
    assert caught.value.code == "invalid_webhook_url"
    assert url not in str(caught.value)


def test_dns_mixed_private_addresses_and_rebinding(monkeypatch):
    def resolve(host, port, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
            for ip in ["8.8.8.8", "10.0.0.1"]
        ]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    with pytest.raises(DeliveryError):
        validate_webhook_url("https://example.org/push")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("8.8.8.8", 443))])
    validate_webhook_url("https://example.org/push")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(DeliveryError):
        send_webhook("https://example.org/push", "测试", "正文")


@pytest.mark.parametrize(
    "host,status,payload,expected",
    [
        ("example.org", 204, b"", None),
        ("example.org", 200, b'{"code":0}', None),
        ("api.day.app", 200, b'{"code":200}', None),
        ("api.day.app", 200, b'{"code":400}', "webhook_rejected"),
        ("api.day.app", 200, b"not-json", "webhook_invalid_response"),
        ("api.day.app", 200, b'{"code":[]}', "webhook_invalid_response"),
        ("example.org", 302, b"", "webhook_rejected"),
        ("example.org", 401, b"", "webhook_rejected"),
        ("example.org", 429, b"", "rate_limited"),
        ("example.org", 503, b"", "webhook_unavailable"),
    ],
)
def test_post_contract_and_response(host, status, payload, expected, public_dns, monkeypatch):
    from ashare_lab import notification_transport as transport

    calls = []

    class Response:
        def read(self, amount):
            return payload

        def getheader(self, name, default=""):
            return "120" if name == "Retry-After" else default

    Response.status = status

    class Connection:
        def __init__(self, hostname, port, address):
            assert hostname == host and port == 443 and address == "8.8.8.8"

        def request(self, method, path, body, headers):
            calls.append((method, path, json.loads(body), headers))

        def getresponse(self):
            return Response()

        def close(self):
            calls.append("closed")

    monkeypatch.setattr(transport, "_PinnedHTTPSConnection", Connection)
    if expected:
        with pytest.raises(DeliveryError) as caught:
            send_webhook(f"https://{host}/synthetic-key?q=1", "测试", "正文")
        assert caught.value.code == expected
        if status == 429:
            assert caught.value.retry_after == 120
        assert caught.value.temporary is (
            status in {429, 503} or expected == "webhook_invalid_response"
        )
    else:
        send_webhook(f"https://{host}/synthetic-key?q=1", "测试", "正文")
    assert calls[0][:3] == (
        "POST",
        "/synthetic-key?q=1",
        {"title": "测试", "body": "正文", "group": "ashare-lab"},
    )
    assert calls[0][3]["Content-Type"] == "application/json; charset=utf-8"
    assert calls[-1] == "closed"


def upgrade(db, version):
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).parents[1] / "src/ashare_lab/migrations")
    )
    with db.engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, version)


def test_bark_retirement_is_atomic_repeatable_and_preserves_history(tmp_path):
    db = Database(tmp_path / "legacy.db")
    upgrade(db, "0005")
    owner = identifier()
    with db.engine.begin() as conn:
        conn.execute(
            owners.insert().values(
                id=owner, session_hash="old", recovery_hash="old", expires_at="2099"
            )
        )
        for kind in ["bark", "email"]:
            conn.execute(
                channels.insert().values(
                    owner_id=owner,
                    type=kind,
                    ciphertext="encrypted",
                    target_hash=kind,
                    verified=True,
                    revision=1,
                    status="active",
                )
            )
        for sid, kinds in [("mixed", ["bark", "email"]), ("only", ["bark"])]:
            conn.execute(
                subscriptions.insert().values(
                    id=sid,
                    owner_id=owner,
                    stock_code="000001.SZ",
                    strategy={"id": "ma_cross"},
                    strategy_version="1",
                    directions=["buy"],
                    channels=kinds,
                    status="active",
                    revision=1,
                    effective_after="2026-10-08",
                    created_at="2026-10-08",
                )
            )
        conn.execute(
            text(
                "INSERT INTO notification_checks VALUES ('check','mixed',1,'2026-10-09','{}','2026-10-09')"
            )
        )
        for did, kind, status in [
            ("pending", "bark", "pending"),
            ("accepted", "bark", "accepted"),
            ("mail", "email", "pending"),
        ]:
            conn.execute(
                deliveries.insert().values(
                    id=did,
                    owner_id=owner,
                    subscription_id="mixed",
                    check_id="check",
                    trade_date="2026-10-09",
                    direction="buy" if did != "accepted" else "sell",
                    channel=kind,
                    revision=1,
                    channel_revision=1,
                    status=status,
                    attempts=0,
                    next_attempt="2026-10-09",
                    created_at="2026-10-09",
                )
            )

    def interrupt(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith("UPDATE notification_subscriptions"):
            raise RuntimeError("migration interrupted")

    event.listen(db.engine, "before_cursor_execute", interrupt)
    try:
        with pytest.raises(RuntimeError, match="migration interrupted"):
            db.initialize()
    finally:
        event.remove(db.engine, "before_cursor_execute", interrupt)
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0005"
        assert conn.execute(select(channels.c.verified).where(channels.c.type == "bark")).scalar()
        assert (
            conn.execute(select(deliveries.c.status).where(deliveries.c.id == "pending")).scalar()
            == "pending"
        )
    db.initialize()
    db.initialize()
    with db.engine.connect() as conn:
        subs = {r["id"]: dict(r) for r in conn.execute(select(subscriptions)).mappings()}
        states = {r["id"]: r["status"] for r in conn.execute(select(deliveries)).mappings()}
        bark = conn.execute(select(channels).where(channels.c.type == "bark")).mappings().one()
        assert states == {"pending": "cancelled", "accepted": "accepted", "mail": "pending"}
        assert subs["mixed"]["channels"] == ["email"] and subs["mixed"]["revision"] == 1
        assert subs["only"]["channels"] == [] and subs["only"]["status"] == "paused"
        assert subs["only"]["revision"] == 2
        assert "重新" in subs["only"]["waiting_reason"]
        assert not bark["verified"] and bark["status"] == "disabled" and bark["revision"] == 2
        assert conn.execute(text("PRAGMA foreign_key_check")).all() == []


def test_pinned_connection_keeps_original_tls_hostname(monkeypatch):
    from ashare_lab import notification_transport as transport

    calls = []

    class Socket:
        def close(self):
            calls.append("closed")

    raw = Socket()

    class Context:
        def wrap_socket(self, connection, *, server_hostname):
            assert connection is raw
            calls.append(server_hostname)
            return raw

    monkeypatch.setattr(transport.ssl, "create_default_context", lambda: Context())
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda address, timeout: calls.append((address, timeout)) or raw,
    )
    connection = transport._PinnedHTTPSConnection("receiver.example.org", 443, "8.8.8.8")
    connection.connect()
    assert calls == [(("8.8.8.8", 443), 15), "receiver.example.org"]
    connection.close()


def test_real_tls_post_to_isolated_receiver(tmp_path, monkeypatch, public_dns, repository):
    """Exercise HTTP and TLS on a local test receiver; never certify production receipt."""
    import ssl
    import threading
    from datetime import UTC, datetime, timedelta
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from ashare_lab import notification_transport as transport
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "receiver.example.org")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("receiver.example.org")]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = tmp_path / "certificate.pem", tmp_path / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_file.chmod(0o600)
    received = []

    class Receiver(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            received.append(
                (
                    self.path,
                    self.headers["Host"],
                    self.headers["Content-Type"],
                    json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                )
            )
            self.send_response(204)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert_file, key_file)
    server.socket = server_context.wrap_socket(server.socket, server_side=True)
    client_context = ssl.create_default_context(cafile=str(cert_file))
    monkeypatch.setattr(transport.ssl, "create_default_context", lambda: client_context)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # Only the dial is redirected. Certificate/hostname validation and HTTP are real.
    # A numeric dial avoids getaddrinfo's intentionally public-only test resolver.
    def dial(address, timeout):
        assert address == ("8.8.8.8", server.server_port)
        connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        connection.settimeout(timeout)
        connection.connect(("127.0.0.1", server.server_port))
        return connection

    monkeypatch.setattr(socket, "create_connection", dial)
    try:
        send_webhook(
            f"https://receiver.example.org:{server.server_port}/notify?q=1",
            "测试通知",
            "真实 HTTP/TLS 正文",
        )
        assert received == [
            (
                "/notify?q=1",
                f"receiver.example.org:{server.server_port}",
                "application/json; charset=utf-8",
                {"title": "测试通知", "body": "真实 HTTP/TLS 正文", "group": "ashare-lab"},
            )
        ]
        from ashare_lab.config import Settings
        from ashare_lab.notifications import NotificationService, SubscriptionRequest
        from ashare_lab.schemas import StrategyConfig
        from cryptography.fernet import Fernet

        service = NotificationService(
            Settings(_env_file=None, notification_encryption_key=Fernet.generate_key().decode()),
            repository.db,
            repository,
        )
        owner = identifier()
        with repository.db.engine.begin() as conn:
            conn.execute(
                owners.insert().values(
                    id=owner,
                    session_hash=identifier(),
                    recovery_hash=identifier(),
                    expires_at="2099",
                )
            )
        service.bind_channel(
            owner, "webhook", f"https://receiver.example.org:{server.server_port}/notify?q=1"
        )
        assert received[-1][3]["body"] == "这是一条测试通知"
        sub = service.save_subscription(
            owner,
            SubscriptionRequest(
                stock_code="000001.SZ",
                strategy=StrategyConfig(id="ma_cross"),
                channels=["webhook"],
            ),
        )
        result = service.test_subscription(owner, sub["id"], "buy")
        assert result["results"] == [{"channel": "webhook", "status": "accepted"}]
        assert received[-1][3]["title"].startswith("【测试】")
        assert "示例信号" in received[-1][3]["body"]
        assert service.list_deliveries(owner)["items"][0]["is_test"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
