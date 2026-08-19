from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import EncryptedJSON
from app.models.mixins import TimestampMixin


class BackupSchedule(TimestampMixin, Base):
    """Send the owner an encrypted backup on a timer.

    The download button is only as good as the habit, and a backup habit is
    exactly the kind that lapses. The free-tier database keeps a few hours of
    point-in-time recovery, so the gap between "I meant to do that" and "I
    needed that" is where a year of records goes.

    The uncomfortable part, recorded here rather than hidden: automating the
    encryption means the passphrase has to live on the server. It is stored
    the way broker keys are -- encrypted at rest with SECRET_ENCRYPTION_KEY --
    but if the whole deployment is what was lost, this copy went with it. The
    owner has to keep their own note, and the form says so before they set it.
    """

    __tablename__ = "backup_schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )

    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    interval_days: Mapped[int] = mapped_column(default=7)
    passphrase_encrypted: Mapped[dict | None] = mapped_column(EncryptedJSON, default=None)
    # None means "wherever the email alert channel already goes". Set when the
    # owner wants archives somewhere other than the address that gets pinged
    # about every order.
    to_addr: Mapped[str | None] = mapped_column(String(255), default=None)

    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # Kept rather than only logged: a backup silently not arriving is the
    # failure this feature exists to prevent, so it has to be visible on the
    # page that offered it.
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
