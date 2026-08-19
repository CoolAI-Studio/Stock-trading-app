import logging
from dataclasses import dataclass
from datetime import UTC

from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import SessionLocal
from app.models.enums import ChannelType, NotificationStatus
from app.models.mixins import utcnow
from app.models.notification import NotificationChannel, NotificationLog
from app.models.user import User
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
    # Held for a quiet window and queued for when it ends. Counted apart from
    # `failed` because nothing went wrong -- and apart from `delivered`
    # because the owner has not seen it yet.
    deferred: int = 0
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
    # Checked here, not only at the bus subscription in main.py: services/alerts.py
    # calls this function directly, so a switch applied only at subscribe time
    # would leave alert-only strategies -- the one pipeline that exists purely
    # to notify -- still notifying after the owner turned notifications off.
    if not settings.NOTIFICATIONS_ENABLED:
        return DispatchResult(error="notifications are disabled")

    user_id = event.data.get("user_id")
    if user_id is None or event.type not in _DISPATCHED_EVENT_TYPES:
        return DispatchResult()
    if event.data.get(DISPATCHED_INLINE_KEY):
        return DispatchResult()

    # Imported here rather than at module scope: retry.py reads SENDERS from
    # this module, so a top-level import in both directions is a cycle.
    from app.services.notification import quiet_hours, retry

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
        owner = session.get(User, user_id)
        owner_timezone = owner.timezone if owner else quiet_hours.DEFAULT_TIMEZONE

        for channel in channels:
            if channel.subscribed_events and event.type not in channel.subscribed_events:
                continue

            sender = SENDERS.get(channel.channel_type)
            if sender is None:
                continue

            # Held, not dropped. The event that fires at 3am is often the one
            # that mattered most, and the owner's alternative -- switching the
            # channel off so it stops waking them -- is how the warnings stop
            # arriving at all. Reuses the retry queue: the sweep delivers it
            # once the window ends.
            if quiet_hours.is_quiet(
                channel.quiet_start_hour, channel.quiet_end_hour, owner_timezone
            ):
                due = quiet_hours.window_ends_at(
                    channel.quiet_start_hour, channel.quiet_end_hour, owner_timezone
                )
                session.add(
                    NotificationLog(
                        user_id=user_id,
                        channel_id=channel.id,
                        order_id=order_id,
                        event=event.type,
                        status=NotificationStatus.FAILED,
                        error=f"靜音時段，將在 {due.astimezone(UTC):%H:%M} UTC 之後送出",
                        message=message,
                        attempts=0,
                        next_retry_at=due,
                    )
                )
                session.commit()
                result.deferred += 1
                continue

            try:
                send_result = sender.send(channel.config_encrypted, message)
            except Exception as exc:
                logger.exception("notification send crashed for channel %s", channel.id)
                ok, error = False, str(exc)
            else:
                ok, error = send_result.ok, send_result.error

            log = NotificationLog(
                user_id=user_id,
                channel_id=channel.id,
                order_id=order_id,
                event=event.type,
                status=NotificationStatus.SENT if ok else NotificationStatus.FAILED,
                error=error,
                # Kept even on success, so the row says what the owner was
                # actually told rather than only that something was sent.
                message=message,
            )
            if not ok:
                # order.created and strategy.error fire once and never again,
                # so without a due time here a ten-second provider outage
                # loses them for good. services/notification/retry.py sweeps
                # what this queues.
                retry.schedule_first_retry(log)
            session.add(log)
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
