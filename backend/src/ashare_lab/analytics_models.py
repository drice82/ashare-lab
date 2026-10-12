"""Anonymous browsing events, separate from research and market data."""

from sqlalchemy import Column, Index, String, Table

from ashare_lab.db import metadata

pageviews = Table(
    "analytics_pageviews",
    metadata,
    Column("event_id", String, primary_key=True),
    Column("visitor_hash", String, nullable=False),
    Column("page", String, nullable=False),
    Column("occurred_at", String, nullable=False),
    Column("local_date", String, nullable=False),
    Index("ix_analytics_pageviews_date_page", "local_date", "page"),
    Index("ix_analytics_pageviews_date_visitor", "local_date", "visitor_hash"),
)
