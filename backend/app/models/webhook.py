from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import utcnow


class TradingViewWebhookLog(Base):
    __tablename__ = "tradingview_webhook_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    received_at: Mapped[datetime] = mapped_column(default=utcnow)
    remote_ip: Mapped[str | None] = mapped_column(String(64), default=None)
    signature_valid: Mapped[bool] = mapped_column(Boolean, default=False)
    # Truncated to 8KB on write by the webhook handler -- this is an audit
    # trail, not a full-fidelity replay log.
    raw_body: Mapped[str] = mapped_column(Text)
    parsed_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"), default=None
    )
    error: Mapped[str | None] = mapped_column(Text, default=None)
