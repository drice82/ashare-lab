"""Account-owned named stock lists."""

from alembic import op

revision = "0009"
down_revision = "0008"


def upgrade() -> None:
    op.execute("UPDATE alembic_version SET version_num=version_num")
    op.execute("""CREATE TABLE watchlists (
        id VARCHAR PRIMARY KEY NOT NULL,
        owner_id VARCHAR NOT NULL REFERENCES notification_owners(id),
        name VARCHAR(80) NOT NULL,
        codes JSON NOT NULL,
        revision INTEGER NOT NULL,
        created_at VARCHAR NOT NULL,
        updated_at VARCHAR NOT NULL,
        CONSTRAINT uq_watchlists_owner_name UNIQUE (owner_id, name)
    )""")
    op.execute("CREATE INDEX ix_watchlists_owner ON watchlists(owner_id)")


def downgrade() -> None:
    op.drop_table("watchlists")
