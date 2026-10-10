"""Append-only coverage ledger instead of quadratic snapshot copies."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    op.create_table(
        "coverage_entries",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("dataset", sa.String, nullable=False),
        sa.Column("key", sa.String, nullable=False),
        sa.Column("version_id", sa.String, sa.ForeignKey("dataset_versions.id"), nullable=False),
        sa.Column("value", sa.JSON, nullable=False),
    )
    op.create_index("ix_coverage_lookup", "coverage_entries", ["dataset", "key", "id"])
    op.create_index("ix_jobs_queue", "jobs", ["status", "created_at"])
    op.create_index("ix_job_logs_task", "job_logs", ["job_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_job_logs_task", table_name="job_logs")
    op.drop_index("ix_jobs_queue", table_name="jobs")
    op.drop_index("ix_coverage_lookup", table_name="coverage_entries")
    op.drop_table("coverage_entries")
