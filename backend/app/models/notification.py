from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text
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
    last_sent_at: Mapped[datetime | None] = mapped_column(default=None)
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
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
