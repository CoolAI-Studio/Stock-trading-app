from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import utcnow


class TradingViewWebhookLog(Base):
    __tablename__ = "tradingview_webhook_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    remote_ip: Mapped[str | None] = mapped_column(String(64), default=None)
    signature_valid: Mapped[bool] = mapped_column(Boolean, default=False)
    # The accepted payload re-serialized with the shared secret removed, then
    # truncated, by the webhook handler -- see app/api/routers/webhooks.py.
    # An audit trail, not a full-fidelity replay log, and deliberately not a
    # second unencrypted copy of the credential that guards the endpoint.
    # Only requests that passed the secret are ever recorded, so the table
    # cannot be grown by an anonymous caller; the handler also prunes it.
    raw_body: Mapped[str] = mapped_column(Text)
    parsed_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"), default=None
    )
    error: Mapped[str | None] = mapped_column(Text, default=None)
    # A remark on a call that SUCCEEDED -- kept apart from `error`, which the
    # UI shows in red. The case it exists for is a symbol that had to be
    # canonicalised (TradingView's {{ticker}} sends 「2330」, never 「2330.TW」):
    # doing that without saying so would be exactly the silent substitution
    # this app refuses everywhere a human is present.
    note: Mapped[str | None] = mapped_column(Text, default=None)
    # This alert carried no `id`, so it is only protected by the
    # identical-body window rather than being properly idempotent. Recorded so
    # the owner learns it from the page instead of from being replayed.
    missing_id: Mapped[bool] = mapped_column(Boolean, default=False)
