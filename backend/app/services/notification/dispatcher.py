import logging

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.enums import ChannelType, NotificationStatus
from app.models.mixins import utcnow
from app.models.notification import NotificationChannel, NotificationLog
from app.services.events import Event
from app.services.notification.email import EmailSender
from app.services.notification.line import LineSender
from app.services.notification.telegram import TelegramSender
from app.services.notification.webpush import WebPushSender

logger = logging.getLogger("app.notifications")

SENDERS = {
    ChannelType.TELEGRAM: TelegramSender(),
    ChannelType.LINE: LineSender(),
    ChannelType.EMAIL: EmailSender(),
    ChannelType.WEB_PUSH: WebPushSender(),
}

_DISPATCHED_EVENT_TYPES = {"order.created", "order.updated", "strategy.error"}


def _format_message(event: Event) -> str:
    if event.type == "order.created":
        return f"New pending order #{event.data.get('order_id')} -- review it in the dashboard."
    if event.type == "order.updated":
        return f"Order #{event.data.get('order_id')} is now {event.data.get('status')}."
    if event.type == "strategy.error":
        return (
            f"Strategy {event.data.get('strategy_id')} was deactivated after repeated "
            f"errors: {event.data.get('error')}"
        )
    return f"{event.type}: {event.data}"


def handle_event(event: Event, db: Session | None = None) -> None:
    """Sync subscriber for services.events.bus (see base.py for why sync is
    fine here). Pass `db` explicitly in tests; production calls (via the
    bus) open and close their own SessionLocal."""
    user_id = event.data.get("user_id")
    if user_id is None or event.type not in _DISPATCHED_EVENT_TYPES:
        return

    owns_session = db is None
    session = db or SessionLocal()
    try:
        channels = (
            session.query(NotificationChannel)
            .filter(
                NotificationChannel.user_id == user_id, NotificationChannel.is_enabled.is_(True)
            )
            .all()
        )
        if not channels:
            return

        message = _format_message(event)
        order_id = event.data.get("order_id")

        for channel in channels:
            if channel.subscribed_events and event.type not in channel.subscribed_events:
                continue

            sender = SENDERS.get(channel.channel_type)
            if sender is None:
                continue

            try:
                result = sender.send(channel.config_encrypted, message)
            except Exception as exc:
                logger.exception("notification send crashed for channel %s", channel.id)
                ok, error = False, str(exc)
            else:
                ok, error = result.ok, result.error

            session.add(
                NotificationLog(
                    user_id=user_id,
                    channel_id=channel.id,
                    order_id=order_id,
                    event=event.type,
                    status=NotificationStatus.SENT if ok else NotificationStatus.FAILED,
                    error=error,
                )
            )
            channel.last_sent_at = utcnow()
            channel.last_error = error
            session.commit()
    finally:
        if owns_session:
            session.close()
