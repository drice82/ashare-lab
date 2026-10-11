"""Frozen V1 notification schema; no imports of live metadata."""

from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade() -> None:
    op.execute("""CREATE TABLE notification_owners (
	id VARCHAR NOT NULL,
	session_hash VARCHAR NOT NULL,
	recovery_hash VARCHAR NOT NULL,
	expires_at VARCHAR NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (session_hash),
	UNIQUE (recovery_hash)
)""")
    op.execute("""CREATE TABLE notification_channels (
	owner_id VARCHAR NOT NULL,
	type VARCHAR NOT NULL,
	ciphertext TEXT NOT NULL,
	target_hash VARCHAR NOT NULL,
	verified BOOLEAN NOT NULL,
	revision INTEGER NOT NULL,
	status VARCHAR NOT NULL,
	PRIMARY KEY (owner_id, type),
	FOREIGN KEY(owner_id) REFERENCES notification_owners (id)
)""")
    op.execute("""CREATE TABLE notification_subscriptions (
	id VARCHAR NOT NULL,
	owner_id VARCHAR NOT NULL,
	stock_code VARCHAR NOT NULL,
	strategy JSON NOT NULL,
	strategy_version VARCHAR NOT NULL,
	directions JSON NOT NULL,
	channels JSON NOT NULL,
	status VARCHAR NOT NULL,
	revision INTEGER NOT NULL,
	effective_after VARCHAR NOT NULL,
	last_check VARCHAR,
	waiting_reason TEXT,
	created_at VARCHAR NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_id) REFERENCES notification_owners (id)
)""")
    op.execute(
        """CREATE INDEX ix_notification_subscriptions_owner_id ON notification_subscriptions (owner_id)"""
    )
    op.execute("""CREATE TABLE notification_checks (
	id VARCHAR NOT NULL,
	subscription_id VARCHAR NOT NULL,
	revision INTEGER NOT NULL,
	trade_date VARCHAR NOT NULL,
	outcome JSON NOT NULL,
	created_at VARCHAR NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (subscription_id, revision, trade_date),
	FOREIGN KEY(subscription_id) REFERENCES notification_subscriptions (id)
)""")
    op.execute("""CREATE TABLE notification_deliveries (
	id VARCHAR NOT NULL,
	owner_id VARCHAR NOT NULL,
	subscription_id VARCHAR NOT NULL,
	check_id VARCHAR NOT NULL,
	trade_date VARCHAR NOT NULL,
	direction VARCHAR NOT NULL,
	channel VARCHAR NOT NULL,
	revision INTEGER NOT NULL,
	channel_revision INTEGER NOT NULL,
	status VARCHAR NOT NULL,
	attempts INTEGER NOT NULL,
	next_attempt VARCHAR NOT NULL,
	error VARCHAR,
	created_at VARCHAR NOT NULL,
	accepted_at VARCHAR,
	PRIMARY KEY (id),
	UNIQUE (subscription_id, trade_date, direction, channel),
	FOREIGN KEY(owner_id) REFERENCES notification_owners (id),
	FOREIGN KEY(subscription_id) REFERENCES notification_subscriptions (id),
	FOREIGN KEY(check_id) REFERENCES notification_checks (id)
)""")
    op.execute(
        """CREATE INDEX ix_notification_deliveries_owner_id ON notification_deliveries (owner_id)"""
    )
    op.execute("""CREATE TABLE notification_verifications (
	owner_id VARCHAR NOT NULL,
	ciphertext TEXT NOT NULL,
	code_hash VARCHAR NOT NULL,
	expires_at VARCHAR NOT NULL,
	attempts INTEGER NOT NULL,
	PRIMARY KEY (owner_id),
	FOREIGN KEY(owner_id) REFERENCES notification_owners (id)
)""")
    op.execute("""CREATE TABLE notification_limits (
	"key" VARCHAR NOT NULL,
	window INTEGER NOT NULL,
	count INTEGER NOT NULL,
	PRIMARY KEY ("key", window)
)""")
    op.execute("""CREATE TABLE notification_service_state (
	"key" VARCHAR NOT NULL,
	value VARCHAR NOT NULL,
	PRIMARY KEY ("key")
)""")


def downgrade() -> None:
    op.drop_table("notification_service_state")
    op.drop_table("notification_limits")
    op.drop_table("notification_verifications")
    op.drop_table("notification_deliveries")
    op.drop_table("notification_checks")
    op.drop_table("notification_subscriptions")
    op.drop_table("notification_channels")
    op.drop_table("notification_owners")
