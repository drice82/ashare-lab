"""Frozen passwordless email authentication; preserve all notification owner IDs."""

from alembic import op

revision = "0005"
down_revision = "0004"


def upgrade() -> None:
    # DML starts SQLite's physical transaction before the following DDL, so an
    # interrupted upgrade rolls back the credential invalidation and new tables.
    # These non-digest placeholders can never match a SHA-256 credential lookup.
    op.execute("""UPDATE notification_owners SET
        session_hash = 'disabled-session:' || id,
        recovery_hash = 'disabled-recovery:' || id,
        expires_at = '1970-01-01T00:00:00+00:00'""")
    op.execute("DELETE FROM notification_verifications")
    op.execute("""CREATE TABLE notification_accounts (
        owner_id VARCHAR NOT NULL PRIMARY KEY REFERENCES notification_owners(id),
        email_hash VARCHAR NOT NULL UNIQUE,
        ciphertext TEXT NOT NULL
    )""")
    op.execute("""CREATE TABLE notification_sessions (
        session_hash VARCHAR NOT NULL PRIMARY KEY,
        owner_id VARCHAR NOT NULL REFERENCES notification_owners(id),
        expires_at VARCHAR NOT NULL
    )""")
    op.execute("CREATE INDEX ix_notification_sessions_owner_id ON notification_sessions(owner_id)")
    op.execute("""CREATE TABLE notification_auth_challenges (
        key VARCHAR NOT NULL PRIMARY KEY,
        purpose VARCHAR NOT NULL,
        owner_id VARCHAR REFERENCES notification_owners(id),
        email_hash VARCHAR NOT NULL,
        code_hash VARCHAR NOT NULL,
        expires_at VARCHAR NOT NULL,
        attempts INTEGER NOT NULL
    )""")


def downgrade() -> None:
    op.drop_table("notification_auth_challenges")
    op.drop_table("notification_sessions")
    op.drop_table("notification_accounts")
    # Never re-enable obsolete authentication credentials on downgrade.
