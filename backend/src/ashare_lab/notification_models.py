"""Notification tables; immutable outcomes and a durable per-channel outbox."""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from ashare_lab.db import metadata

owners = Table(
    "notification_owners",
    metadata,
    Column("id", String, primary_key=True),
    Column("session_hash", String, unique=True, nullable=False),
    Column("recovery_hash", String, unique=True, nullable=False),
    Column("expires_at", String, nullable=False),
)
channels = Table(
    "notification_channels",
    metadata,
    Column("owner_id", String, ForeignKey(owners.c.id), primary_key=True),
    Column("type", String, primary_key=True),
    Column("ciphertext", Text, nullable=False),
    Column("target_hash", String, nullable=False),
    Column("verified", Boolean, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("status", String, nullable=False),
)
subscriptions = Table(
    "notification_subscriptions",
    metadata,
    Column("id", String, primary_key=True),
    Column("owner_id", String, ForeignKey(owners.c.id), nullable=False, index=True),
    Column("stock_code", String, nullable=False),
    Column("strategy", JSON, nullable=False),
    Column("strategy_version", String, nullable=False),
    Column("directions", JSON, nullable=False),
    Column("channels", JSON, nullable=False),
    Column("status", String, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("effective_after", String, nullable=False),
    Column("last_check", String),
    Column("waiting_reason", Text),
    Column("created_at", String, nullable=False),
)
checks = Table(
    "notification_checks",
    metadata,
    Column("id", String, primary_key=True),
    Column("subscription_id", String, ForeignKey(subscriptions.c.id), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("trade_date", String, nullable=False),
    Column("outcome", JSON, nullable=False),
    Column("created_at", String, nullable=False),
    UniqueConstraint("subscription_id", "revision", "trade_date"),
)
deliveries = Table(
    "notification_deliveries",
    metadata,
    Column("id", String, primary_key=True),
    Column("owner_id", String, ForeignKey(owners.c.id), nullable=False, index=True),
    Column("subscription_id", String, ForeignKey(subscriptions.c.id), nullable=False),
    Column("check_id", String, ForeignKey(checks.c.id), nullable=False),
    Column("trade_date", String, nullable=False),
    Column("direction", String, nullable=False),
    Column("channel", String, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("channel_revision", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("next_attempt", String, nullable=False),
    Column("error", String),
    Column("created_at", String, nullable=False),
    Column("accepted_at", String),
    UniqueConstraint("subscription_id", "trade_date", "direction", "channel"),
)
verifications = Table(
    "notification_verifications",
    metadata,
    Column("owner_id", String, ForeignKey(owners.c.id), primary_key=True),
    Column("ciphertext", Text, nullable=False),
    Column("code_hash", String, nullable=False),
    Column("expires_at", String, nullable=False),
    Column("attempts", Integer, nullable=False),
)
limits = Table(
    "notification_limits",
    metadata,
    Column("key", String, primary_key=True),
    Column("window", Integer, primary_key=True),
    Column("count", Integer, nullable=False),
)
service_state = Table(
    "notification_service_state",
    metadata,
    Column("key", String, primary_key=True),
    Column("value", String, nullable=False),
)
accounts = Table(
    "notification_accounts",
    metadata,
    Column("owner_id", String, ForeignKey(owners.c.id), primary_key=True),
    Column("email_hash", String, unique=True, nullable=False),
    Column("ciphertext", Text, nullable=False),
)
sessions = Table(
    "notification_sessions",
    metadata,
    Column("session_hash", String, primary_key=True),
    Column("owner_id", String, ForeignKey(owners.c.id), nullable=False, index=True),
    Column("expires_at", String, nullable=False),
)
auth_challenges = Table(
    "notification_auth_challenges",
    metadata,
    Column("key", String, primary_key=True),
    Column("purpose", String, nullable=False),
    Column("owner_id", String, ForeignKey(owners.c.id)),
    Column("email_hash", String, nullable=False),
    Column("code_hash", String, nullable=False),
    Column("expires_at", String, nullable=False),
    Column("attempts", Integer, nullable=False),
)
TABLES = [
    owners,
    channels,
    subscriptions,
    checks,
    deliveries,
    verifications,
    limits,
    service_state,
    accounts,
    sessions,
    auth_challenges,
]


# Manual samples are isolated from immutable signal checks and their outbox.
test_requests = Table(
    "notification_test_requests",
    metadata,
    Column("id", String, primary_key=True),
    Column("owner_id", String, ForeignKey(owners.c.id), nullable=False, index=True),
    Column("timestamp", Float, nullable=False),
)
test_deliveries = Table(
    "notification_test_deliveries",
    metadata,
    Column("id", String, primary_key=True),
    Column("request_id", String, ForeignKey(test_requests.c.id), nullable=False),
    Column("owner_id", String, ForeignKey(owners.c.id), nullable=False, index=True),
    Column("subscription_id", String, ForeignKey(subscriptions.c.id), nullable=False),
    Column("trade_date", String, nullable=False),
    Column("direction", String, nullable=False),
    Column("channel", String, nullable=False),
    Column("status", String, nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("error", String),
    Column("created_at", String, nullable=False),
    Column("accepted_at", String),
    Column("outcome", JSON, nullable=False),
)
