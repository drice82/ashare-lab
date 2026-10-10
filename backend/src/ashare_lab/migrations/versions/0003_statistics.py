"""Rebuildable statistics for immutable files and data versions."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade() -> None:
    op.create_table(
        "partition_statistics",
        sa.Column("path", sa.String, primary_key=True),
        sa.Column("format_version", sa.Integer, primary_key=True),
        sa.Column("value", sa.JSON, nullable=False),
    )
    op.create_table(
        "dataset_summaries",
        sa.Column("version_id", sa.String, sa.ForeignKey("dataset_versions.id"), primary_key=True),
        sa.Column("format_version", sa.Integer, primary_key=True),
        sa.Column("value", sa.JSON, nullable=False),
        sa.Column("created_at", sa.String, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("dataset_summaries")
    op.drop_table("partition_statistics")
