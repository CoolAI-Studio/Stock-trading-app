from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import EncryptedJSON
from app.models.enums import ChannelType, NotificationStatus
from app.models.mixins import TimestampMixin, utcnow


class NotificationChannel(TimestampMixin, Base):
    __tablename__ = "notification_channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    channel_type: Mapped[ChannelType] = mapped_column(
        SAEnum(ChannelType, native_enum=False, length=16)
    )
    label: Mapped[str] = mapped_column(String(120))
    # Fernet-encrypted at rest -- e.g. {"bot_token": ..., "chat_id": ...}. See
    # app/db/types.py::EncryptedJSON.
    config_encrypted: Mapped[dict] = mapped_column(EncryptedJSON)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    subscribed_events: Mapped[list | None] = mapped_column(JSON, default=None)
    # Hours in the owner's own timezone during which this channel stays
    # silent. Both None means always on. Per channel rather than per account,
    # because the point is to let email keep arriving while the phone sleeps.
    quiet_start_hour: Mapped[int | None] = mapped_column(default=None)
    quiet_end_hour: Mapped[int | None] = mapped_column(default=None)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)


class NotificationLog(Base):
    __tablename__ = "notification_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("notification_channels.id", ondelete="CASCADE")
    )
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"), default=None
    )

    event: Mapped[str] = mapped_column(String(64))
    status: Mapped[NotificationStatus] = mapped_column(
        SAEnum(NotificationStatus, native_enum=False, length=16)
    )
    error: Mapped[str | None] = mapped_column(Text, default=None)

    # What was actually sent. Kept because the event that produced it is long
    # gone by the time a retry runs, and re-rendering it from scratch is not
    # possible -- there is nothing left to render from. NULL on rows written
    # before retries existed, which is exactly why those are never retried:
    # inventing the text would mean telling the owner about something that
    # may no longer be true.
    message: Mapped[str | None] = mapped_column(Text, default=None)
    # How many sends have been made for this one notification, first attempt
    # included.
    attempts: Mapped[int] = mapped_column(default=1)
    # When the next attempt is due. NULL means nothing more is owed: delivered,
    # given up on, or a channel the owner switched off.
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
