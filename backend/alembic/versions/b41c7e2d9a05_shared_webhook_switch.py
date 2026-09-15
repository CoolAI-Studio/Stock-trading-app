"""A switch to close the old shared TradingView URL, and when it was last used.

Revision ID: b41c7e2d9a05
Revises: 737fe85e9e9e
Create Date: 2026-09-15 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b41c7e2d9a05"
down_revision: str | Sequence[str] | None = "737fe85e9e9e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        # server_default, for the same reason eaca5d3b0e2b needed one: every
        # deployment with an owner has a row to fill, and NOT NULL with nothing
        # to fill it is an error on Postgres -- where login itself would fail,
        # since every query that loads a User selects this column.
        #
        # And TRUE, not false: an existing owner's old alerts are still calling
        # the shared URL. Starting it closed would silence every one of them on
        # the day of the update (#50). Closing it is his decision, on the page
        # that shows him when it was last used.
        batch_op.add_column(
            sa.Column(
                "shared_webhook_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch_op.add_column(
            sa.Column("shared_webhook_last_used_at", sa.DateTime(timezone=True), nullable=True)
        )

    # The default existed only to fill those rows; the model supplies the value
    # from here on (the same second step eaca5d3b0e2b and c4ee0993ef5e took).
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("shared_webhook_enabled", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("shared_webhook_last_used_at")
        batch_op.drop_column("shared_webhook_enabled")
