"""strategies carry the parameters their owner tuned

Revision ID: 4ee158efc287
Revises: bfefc77ba408
Create Date: 2026-08-21 15:21:55.404605

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4ee158efc287"
down_revision: str | Sequence[str] | None = "bfefc77ba408"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("strategies", schema=None) as batch_op:
        batch_op.add_column(sa.Column("params", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("strategies", schema=None) as batch_op:
        batch_op.drop_column("params")
