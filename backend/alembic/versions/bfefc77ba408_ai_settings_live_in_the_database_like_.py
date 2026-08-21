"""ai settings live in the database like every other secret

Revision ID: bfefc77ba408
Revises: a5c92df44199
Create Date: 2026-08-21 13:21:03.162168

"""

from collections.abc import Sequence

import sqlalchemy as sa

import app.db.types
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bfefc77ba408"
down_revision: str | Sequence[str] | None = "a5c92df44199"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "ai_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("base_url", sa.String(length=255), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("api_key_encrypted", app.db.types.EncryptedJSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_ai_settings_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_settings")),
        sa.UniqueConstraint("user_id", name=op.f("uq_ai_settings_user_id")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("ai_settings")
