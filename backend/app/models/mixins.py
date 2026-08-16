from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    """created_at/updated_at with Python-side defaults.

    Deliberately not `server_default=func.now()` -- SQLite and Postgres disagree
    on timezone handling for that, and app-level timestamps are consistent
    across both backends.
    """

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
