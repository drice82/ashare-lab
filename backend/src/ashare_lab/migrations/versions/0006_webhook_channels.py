"""Retire Bark bindings; preserve email delivery and all immutable history."""

import json

from alembic import op
from sqlalchemy import text

revision = "0006"
down_revision = "0005"


def upgrade() -> None:
    # Start SQLite's physical write transaction before any reads. All retirement
    # changes and Alembic's revision marker commit or roll back together.
    op.execute("""UPDATE notification_channels
        SET verified=0, status='disabled', revision=revision+1 WHERE type='bark'""")
    op.execute("""UPDATE notification_deliveries SET status='cancelled'
        WHERE channel='bark' AND status IN ('pending','sending')""")
    connection = op.get_bind()
    rows = (
        connection.execute(
            text("""SELECT id, channels, status FROM notification_subscriptions
        WHERE status!='deleted'""")
        )
        .mappings()
        .all()
    )
    for row in rows:
        selected = json.loads(row["channels"])
        if "bark" not in selected:
            continue
        selected = [kind for kind in selected if kind != "bark"]
        if selected:
            # Only a channel was removed; don't invalidate queued email deliveries.
            connection.execute(
                text("""UPDATE notification_subscriptions SET channels=:channels
                WHERE id=:id"""),
                {"channels": json.dumps(selected), "id": row["id"]},
            )
        else:
            connection.execute(
                text("""UPDATE notification_subscriptions
                SET channels='[]', status='paused', revision=revision+1,
                waiting_reason='旧 Bark 渠道已停用，请重新绑定 Webhook 并修改订阅'
                WHERE id=:id"""),
                {"id": row["id"]},
            )


def downgrade() -> None:
    # Retired credentials/queues cannot be safely re-enabled or re-sent automatically.
    pass
