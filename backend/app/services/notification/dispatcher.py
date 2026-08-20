import logging
from dataclasses import dataclass
from datetime import UTC, timedelta

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

# How long one "reached nobody" row absorbs later misses. Long enough to bound
# a five-second poll to 24 rows a day rather than 17,280, short enough that the
# ledger still reflects what is happening now rather than this morning.
_NOBODY_FOLD_WINDOW = timedelta(hours=1)

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
        message = _format_message(event)

        if not channels:
            # An alert nobody was told about is this product's critical
            # failure, and it used to leave nothing behind at all.
            _record_reaching_nobody(session, user_id, event, message, has_channels=False)
            return result

        order_id = event.data.get("order_id")
        owner = session.get(User, user_id)
        owner_timezone = owner.timezone if owner else quiet_hours.DEFAULT_TIMEZONE

        for channel in channels:
            if channel.subscribed_events and event.type not in channel.subscribed_events:
                continue

            sender = SENDERS.get(channel.channel_type)
            if sender is None:
                continue

            # Everything from here to the commit is wrapped. Only sender.send
            # used to be, so a raise anywhere else -- the quiet-hours
            # calculation, schedule_first_retry, either commit -- escaped the
            # whole loop and the channels after this one were never attempted.
            # Having several channels is meant to be what makes one failing
            # survivable; that made one failing take the rest down with it.
            try:
                outcome = _deliver_to_channel(
                    session, channel, event, message, order_id, user_id, owner_timezone
                )
            except Exception as exc:
                logger.exception("dispatch to channel %s crashed", channel.id)
                # Recorded rather than swallowed: an invisible failure is the
                # worse trade for this product. Queued for retry too, because
                # this event fires once and a code fault is no reason to lose
                # it for good.
                log = NotificationLog(
                    user_id=user_id,
                    channel_id=channel.id,
                    order_id=order_id,
                    event=event.type,
                    status=NotificationStatus.FAILED,
                    error=f"送出時發生未預期的錯誤：{exc}"[:500],
                    message=message,
                )
                retry.schedule_first_retry(log)
                session.add(log)
                session.commit()
                result.failed += 1
                result.error = str(exc)
                continue

            if outcome == "deferred":
                result.deferred += 1
            elif outcome == "sent":
                result.delivered += 1
            else:
                result.failed += 1
                result.error = outcome
            continue

        if result.delivered + result.failed + result.deferred == 0:
            # Channels exist, and every one of them filtered this event type
            # out. Different cause from having no channel, so a different
            # message: telling somebody to create a channel they already have
            # sends them the wrong way.
            _record_reaching_nobody(session, user_id, event, message, has_channels=True)
    finally:
        if owns_session:
            session.close()

    return result


def _record_reaching_nobody(
    session, user_id: int, event: Event, message: str, *, has_channels: bool
) -> None:
    """Leave a row saying this alert reached nobody.

    DispatchResult's own docstring has always said that delivered == 0 is a
    failure even when nothing raised. Nothing acted on it: the dispatcher
    returned, and the 發送紀錄 ledger then looked exactly like an afternoon on
    which nothing had happened. The owner could not find the failure even by
    going and looking for it, which for an alerting product is worse than the
    alert failing loudly.

    channel_id is NULL because there is no channel involved -- that is the
    whole point of the row. No retry is scheduled either: there is nothing to
    retry it TO, and a due date would make the sweep pick it up forever.
    """
    # ONE ROW PER WINDOW, because alerts.py documents alert_interval_sec = 0 as
    # "notify every time" and applies no cap of its own. With no channels that
    # wrote a row on every poll -- 17,280 a day at the five-second default, on a
    # free-tier database, burying the very row this exists to make visible.
    #
    # Folded rather than dropped: the count goes on `attempts`, so the ledger
    # still distinguishes one missed alert from fifty.
    recent = (
        session.query(NotificationLog)
        .filter(
            NotificationLog.user_id == user_id,
            NotificationLog.channel_id.is_(None),
            NotificationLog.created_at >= utcnow() - _NOBODY_FOLD_WINDOW,
        )
        .order_by(NotificationLog.id.desc())
        .first()
    )

    reason = (
        "有啟用中的通知管道，但沒有任何一個訂閱了這個事件類型，所以這則提醒沒有送到任何地方。"
        "請到「通知」頁，在其中一個管道勾選這個事件。"
        if has_channels
        else "沒有任何啟用中的通知管道，所以這則提醒沒有送到任何地方。"
        "請到「通知」頁建立一個管道（Telegram、Email、LINE 或瀏覽器推播都可以）。"
    )
    if recent is not None:
        recent.attempts += 1
        recent.error = f"{reason}（這段期間共有 {recent.attempts} 則提醒沒有送到）"
        session.commit()
        return

    session.add(
        NotificationLog(
            user_id=user_id,
            channel_id=None,
            order_id=event.data.get("order_id"),
            event=event.type,
            status=NotificationStatus.FAILED,
            error=f"{reason}（這段期間共有 1 則提醒沒有送到）",
            # Kept so the row says WHAT went unheard, not merely that something
            # did -- otherwise the owner cannot tell whether it mattered.
            message=message,
            attempts=1,
        )
    )
    session.commit()


def _deliver_to_channel(
    session,
    channel: NotificationChannel,
    event: Event,
    message: str,
    order_id: int | None,
    user_id: int,
    owner_timezone: str | None,
) -> str:
    """Send one alert through one channel, and say what happened.

    Returns "sent", "deferred", or the error string. Extracted so the caller
    can wrap the WHOLE of it: previously only sender.send was inside a try, and
    a raise anywhere else here -- the quiet-hours calculation, either commit,
    schedule_first_retry -- escaped the per-channel loop and silently skipped
    every channel after this one.
    """
    from app.services.notification import quiet_hours, retry

    sender = SENDERS[channel.channel_type]

    # Held, not dropped. The event that fires at 3am is often the one that
    # mattered most, and the owner's alternative -- switching the channel off
    # so it stops waking them -- is how the warnings stop arriving at all.
    # Reuses the retry queue: the sweep delivers it once the window ends.
    if quiet_hours.is_quiet(channel.quiet_start_hour, channel.quiet_end_hour, owner_timezone):
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
        return "deferred"

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
        # Kept even on success, so the row says what the owner was actually
        # told rather than only that something was sent.
        message=message,
    )
    if not ok:
        # order.created and strategy.error fire once and never again, so
        # without a due time here a ten-second provider outage loses them for
        # good. services/notification/retry.py sweeps what this queues.
        retry.schedule_first_retry(log)
    session.add(log)
    channel.last_sent_at = utcnow()
    channel.last_error = error
    session.commit()

    return "sent" if ok else (error or "unknown error")
