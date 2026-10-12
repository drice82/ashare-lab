"""Hand-checkable anonymous page view counts and isolated storage upgrades."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from ashare_lab.analytics import AnalyticsService, PageViewRequest
from ashare_lab.analytics_models import pageviews
from ashare_lab.api import create_app
from ashare_lab.config import Settings
from ashare_lab.db import Database
from ashare_lab.services import ServiceError
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import event, select, text


def service_at(tmp_path: Path, stamp: datetime) -> AnalyticsService:
    db = Database(tmp_path / "business.db")
    db.initialize()
    return AnalyticsService(db, "Asia/Shanghai", clock=lambda: stamp)


def record(service: AnalyticsService, visitor: str, page: str = "screening") -> dict[str, str]:
    payload = {"event_id": str(uuid4()), "visitor_id": visitor, "page": page}
    service.record(PageViewRequest.model_validate(payload))
    return payload


def test_hand_count_idempotency_and_private_storage(tmp_path: Path) -> None:
    service = service_at(tmp_path, datetime(2026, 10, 11, tzinfo=UTC))
    a, b = str(uuid4()), str(uuid4())
    first = record(service, a)
    record(service, a)
    record(service, a, "knowledge")
    record(service, b)
    service.record(PageViewRequest.model_validate(first))
    result = service.summary(date(2026, 10, 11), date(2026, 10, 11))
    assert result["totals"] == {"pageviews": 4, "visitors": 2}
    assert result["daily"] == [{"date": "2026-10-11", "pageviews": 4, "visitors": 2}]
    assert [
        (row["page"], row["pageviews"], row["visitors"], row["share"]) for row in result["pages"]
    ] == [("screening", 3, 2, 0.75), ("knowledge", 1, 1, 0.25)]
    with service.db.engine.connect() as conn:
        rows = conn.execute(select(pageviews)).mappings().all()
    assert len(rows) == 4
    assert a not in str(rows) and b not in str(rows)
    assert set(rows[0]) == {"event_id", "visitor_hash", "page", "occurred_at", "local_date"}


def test_timezone_midnight_range_distinct_and_zero_days(tmp_path: Path) -> None:
    stamp = datetime(2026, 10, 10, 15, 59, tzinfo=UTC)
    service = service_at(tmp_path, stamp)
    visitor = str(uuid4())
    record(service, visitor)
    service.clock = lambda: stamp + timedelta(minutes=1)
    record(service, visitor, "knowledge")
    result = service.summary(date(2026, 10, 9), date(2026, 10, 11))
    assert result["totals"] == {"pageviews": 2, "visitors": 1}
    assert result["daily"] == [
        {"date": "2026-10-09", "pageviews": 0, "visitors": 0},
        {"date": "2026-10-10", "pageviews": 1, "visitors": 1},
        {"date": "2026-10-11", "pageviews": 1, "visitors": 1},
    ]


def test_retention_cleanup_after_restart_and_on_read(tmp_path: Path) -> None:
    stamp = datetime(2026, 7, 13, tzinfo=UTC)
    service = service_at(tmp_path, stamp)
    record(service, str(uuid4()))
    service.clock = lambda: stamp + timedelta(days=1)
    record(service, str(uuid4()))
    restarted = AnalyticsService(
        service.db, "Asia/Shanghai", clock=lambda: stamp + timedelta(days=90)
    )
    result = restarted.summary(None, None)
    assert result["earliest_date"] == "2026-07-14"
    assert result["retention_days"] == 90
    assert result["totals"]["pageviews"] == 0  # default last 30 days
    with service.db.engine.connect() as conn:
        assert conn.execute(select(pageviews.c.local_date)).scalars().all() == ["2026-07-14"]


@pytest.mark.parametrize(
    "start,end",
    [("2026-07-13", "2026-10-11"), ("2026-10-12", "2026-10-12"), ("2026-10-11", "2026-10-10")],
)
def test_invalid_ranges(tmp_path: Path, start: str, end: str) -> None:
    service = service_at(tmp_path, datetime(2026, 10, 11, tzinfo=UTC))
    with pytest.raises(ServiceError) as error:
        service.summary(date.fromisoformat(start), date.fromisoformat(end))
    assert error.value.status == 422


def test_public_ingest_exact_route_and_protected_summary(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        database_path=tmp_path / "business.db",
        api_access_token="isolated-api",
        data_management_password="isolated-admin",
    )
    client = TestClient(create_app(settings))
    payload = {"event_id": str(uuid4()), "visitor_id": str(uuid4()), "page": "screening"}
    endpoint = "/api/v1/analytics/pageviews"
    assert client.post(endpoint, json=payload).status_code == 403
    assert (
        client.post(
            endpoint, json=payload, headers={"Origin": "https://untrusted.example"}
        ).status_code
        == 403
    )
    assert (
        client.post(endpoint, json=payload, headers={"Origin": "http://testserver"}).status_code
        == 204
    )
    assert (
        client.post(endpoint, json=payload, headers={"Origin": "http://testserver"}).status_code
        == 204
    )
    assert client.get(endpoint).status_code == 401
    assert client.post(endpoint + "/unknown", json=payload).status_code == 401
    invalid = client.post(
        endpoint,
        json={**payload, "page": "data", "url": "private"},
        headers={"Origin": "http://testserver"},
    )
    assert invalid.status_code == 422 and "private" not in invalid.text
    summary = "/api/v1/data/analytics"
    assert client.get(summary).status_code == 401
    bearer = {"Authorization": "Bearer isolated-api"}
    assert client.get(summary, headers=bearer).status_code == 403
    response = client.get(
        summary, headers={**bearer, "X-Data-Management-Password": "isolated-admin"}
    )
    assert response.status_code == 200 and response.json()["totals"] == {
        "pageviews": 1,
        "visitors": 1,
    }
    assert response.headers["Cache-Control"] == "no-store"


def test_upgrade_interruption_repeat_and_old_records(tmp_path: Path) -> None:
    from alembic import command
    from alembic.config import Config

    db = Database(tmp_path / "business.db")
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).parents[1] / "src/ashare_lab/migrations")
    )
    with db.engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "0007")
        conn.execute(
            text("INSERT INTO settings (key, value) VALUES ('old-record', :value)"),
            {"value": '{"value":42}'},
        )

    def interrupt(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        if statement.startswith("CREATE INDEX ix_analytics_pageviews"):
            raise RuntimeError("simulated interruption")

    event.listen(db.engine, "before_cursor_execute", interrupt)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        db.initialize()
    event.remove(db.engine, "before_cursor_execute", interrupt)
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0007"
        assert (
            conn.execute(
                text("SELECT name FROM sqlite_master WHERE name='analytics_pageviews'")
            ).first()
            is None
        )
    db.initialize()
    db.initialize()
    with db.engine.connect() as conn:
        assert (
            conn.execute(text("SELECT value FROM settings WHERE key='old-record'")).scalar_one()
            == '{"value":42}'
        )
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0009"


def test_primary_domain_and_alias_accept_browsing_and_notification_writes(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        database_path=tmp_path / "business.db",
        notification_public_url="https://niu.kaistar.net",
        public_url_aliases="https://niuniu.kaistar.net",
    )
    client = TestClient(create_app(settings))
    for origin in ["https://niu.kaistar.net", "https://niuniu.kaistar.net"]:
        payload = {"event_id": str(uuid4()), "visitor_id": str(uuid4()), "page": "knowledge"}
        assert (
            client.post(
                "/api/v1/analytics/pageviews", json=payload, headers={"Origin": origin}
            ).status_code
            == 204
        )
        assert (
            client.delete("/api/v1/notifications/session", headers={"Origin": origin}).status_code
            == 200
        )
    assert (
        client.delete(
            "/api/v1/notifications/session", headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "alias", ["https://niuniu.kaistar.net/private", "https://user@niuniu.kaistar.net"]
)
def test_public_aliases_must_be_plain_origins(alias: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, public_url_aliases=alias)
