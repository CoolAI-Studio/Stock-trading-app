"""A version number for each account's personal TradingView URL (#115).

Revision ID: eaca5d3b0e2b
Revises: 09da5bd6bc45
Create Date: 2026-09-15 01:29:09.688513

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "eaca5d3b0e2b"
down_revision: str | Sequence[str] | None = "09da5bd6bc45"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        # server_default, because autogenerate wrote this NOT NULL with nothing
        # to fill the rows that already exist -- which is every deployment that
        # has an owner. On Postgres that is an error, and start.py deliberately
        # keeps an owned deployment serving on the old schema rather than locking
        # it (#102). Every query that loads a User selects this column, so the
        # symptom would not have been one broken page: login itself would fail.
        # Existing accounts start at version 0, the same as a new one.
        batch_op.add_column(
            sa.Column("webhook_url_version", sa.Integer(), nullable=False, server_default="0")
        )

    # The default existed only to fill those rows. The model supplies the value
    # from here on, and keeping it would leave the schema disagreeing with the
    # model -- the same second step c4ee0993ef5e took for token_version.
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("webhook_url_version", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("webhook_url_version")
