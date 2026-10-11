"""Isolated manual samples and persistent account test quotas."""

from alembic import op

revision = "0007"
down_revision = "0006"


def upgrade() -> None:
    # Start SQLite's physical transaction before DDL; interruption rolls back
    # both new tables and the Alembic revision marker.
    op.execute("UPDATE alembic_version SET version_num=version_num")
    op.execute("""CREATE TABLE notification_test_requests (
        id VARCHAR PRIMARY KEY NOT NULL,
        owner_id VARCHAR NOT NULL REFERENCES notification_owners(id),
        timestamp FLOAT NOT NULL
    )""")
    op.execute(
        "CREATE INDEX ix_notification_test_requests_owner_id ON notification_test_requests(owner_id)"
    )
    op.execute("""CREATE TABLE notification_test_deliveries (
        id VARCHAR PRIMARY KEY NOT NULL,
        request_id VARCHAR NOT NULL REFERENCES notification_test_requests(id),
        owner_id VARCHAR NOT NULL REFERENCES notification_owners(id),
        subscription_id VARCHAR NOT NULL REFERENCES notification_subscriptions(id),
        trade_date VARCHAR NOT NULL, direction VARCHAR NOT NULL,
        channel VARCHAR NOT NULL, status VARCHAR NOT NULL,
        attempts INTEGER NOT NULL, error VARCHAR,
        created_at VARCHAR NOT NULL, accepted_at VARCHAR, outcome JSON NOT NULL
    )""")
    op.execute(
        "CREATE INDEX ix_notification_test_deliveries_owner_id ON notification_test_deliveries(owner_id)"
    )


def downgrade() -> None:
    op.drop_table("notification_test_deliveries")
    op.drop_table("notification_test_requests")
