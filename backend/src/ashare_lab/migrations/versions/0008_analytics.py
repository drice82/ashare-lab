"""Anonymous page views with bounded retention."""

from alembic import op

revision = "0008"
down_revision = "0007"


def upgrade() -> None:
    # Ensure SQLite DDL and the revision marker roll back together on interruption.
    op.execute("UPDATE alembic_version SET version_num=version_num")
    op.execute("""CREATE TABLE analytics_pageviews (
        event_id VARCHAR PRIMARY KEY NOT NULL,
        visitor_hash VARCHAR NOT NULL,
        page VARCHAR NOT NULL,
        occurred_at VARCHAR NOT NULL,
        local_date VARCHAR NOT NULL
    )""")
    op.execute(
        "CREATE INDEX ix_analytics_pageviews_date_page ON analytics_pageviews(local_date, page)"
    )
    op.execute(
        "CREATE INDEX ix_analytics_pageviews_date_visitor ON analytics_pageviews(local_date, visitor_hash)"
    )


def downgrade() -> None:
    op.drop_table("analytics_pageviews")
