"""queue a failed notification for retry

Revision ID: 173b1defdee1
Revises: 9e1c67c235ba
Create Date: 2026-08-19 15:28:58.417247

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "173b1defdee1"
down_revision: str | Sequence[str] | None = "9e1c67c235ba"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("notification_logs", schema=None) as batch_op:
        # NULL on every existing row, and that is load-bearing: retry.py skips
        # a row with no message rather than inventing text and telling the
        # owner about something that may no longer be true.
        batch_op.add_column(sa.Column("message", sa.Text(), nullable=True))
        # server_default, not just a Python default: autogenerate wrote this
        # NOT NULL with nothing to backfill, which fails outright on a table
        # that already has rows -- and notification_logs on the live database
        # certainly does.
        batch_op.add_column(sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"))
        batch_op.add_column(sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_notification_logs_next_retry_at"), ["next_retry_at"], unique=False
        )

    # Dropped once the backfill has happened: the application always supplies
    # the value, and leaving a database-side default around invites a future
    # insert that silently relies on it.
    with op.batch_alter_table("notification_logs", schema=None) as batch_op:
        batch_op.alter_column("attempts", server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("notification_logs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_notification_logs_next_retry_at"))
        batch_op.drop_column("next_retry_at")
        batch_op.drop_column("attempts")
        batch_op.drop_column("message")

    # ### end Alembic commands ###
