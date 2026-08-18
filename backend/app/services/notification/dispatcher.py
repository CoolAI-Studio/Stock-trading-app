import logging
from dataclasses import dataclass

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

_DISPATCHED_EVENT_TYPES = {"order.created", "order.updated", "strategy.error", "strategy.alert"}

# Stamped on an event whose channels the caller already dispatched itself.
# services/alerts.py has to send synchronously -- it needs to know whether
# the owner actually saw the alert before it may start the throttle clock --
# but the event still goes on the bus afterwards for the WS and log
# subscribers. Without this key the bus-subscribed dispatcher would send a
# second copy of every alert.
DISPATCHED_INLINE_KEY = "dispatched_inline"


@dataclass
class DispatchResult:
    """What actually reached the user. `delivered == 0` is a failure even when
    nothing raised -- a user with no enabled channel, or one whose only
    channel is filtered out by subscribed_events, was not notified."""

    delivered: int = 0
    failed: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.delivered > 0


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
    if event.type == "strategy.alert":
        side = str(event.data.get("side", "")).upper()
        return (
            f"[Alert only] {event.data.get('strategy_name')}: {side} "
            f"{event.data.get('symbol')} @ {event.data.get('price')} -- "
            f"no order was created."
        )
    return f"{event.type}: {event.data}"


def handle_event(event: Event, db: Session | None = None) -> DispatchResult:
    """Sync subscriber for services.events.bus (see base.py for why sync is
    fine here). Pass `db` explicitly in tests; production calls (via the
    bus) open and close their own SessionLocal.

    Returns what was delivered. The bus discards the return value; callers
    that need to know whether the user was actually reached (services/alerts.py)
    call this directly."""
    user_id = event.data.get("user_id")
    if user_id is None or event.type not in _DISPATCHED_EVENT_TYPES:
        return DispatchResult()
    if event.data.get(DISPATCHED_INLINE_KEY):
        return DispatchResult()

    result = DispatchResult()
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
            return result

        message = _format_message(event)
        order_id = event.data.get("order_id")

        for channel in channels:
            if channel.subscribed_events and event.type not in channel.subscribed_events:
                continue

            sender = SENDERS.get(channel.channel_type)
            if sender is None:
                continue

            try:
                send_result = sender.send(channel.config_encrypted, message)
            except Exception as exc:
                logger.exception("notification send crashed for channel %s", channel.id)
                ok, error = False, str(exc)
            else:
                ok, error = send_result.ok, send_result.error

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

            if ok:
                result.delivered += 1
            else:
                result.failed += 1
                result.error = error
    finally:
        if owns_session:
            session.close()

    return result
