"""Re-send notifications whose first delivery failed.

An alert that never arrives is this product's critical failure. It watches the
market so the owner does not have to, which only works if the message gets
through -- and the two events that most need to arrive, a pending order and a
strategy that just disabled itself, are exactly the two that fire once and
never again.

Alert-only strategies already survive a blip, but by accident of shape rather
than design: the strategy re-fires every tick, so the next signal retries the
delivery (see services/alerts.py). Nothing gave the one-shot events the same
second chance, so a ten-second Telegram outage swallowed them silently.

The queue is the notification_logs table itself -- a FAILED row with a
`next_retry_at` in the past is the work item. No separate table, no broker: at
this scale the sweep is one indexed query per poll, and keeping the attempt
history and the retry state in the same row is what makes a dead channel
legible afterwards instead of just quiet.
"""

import logging
from datetime import timedelta

from sqlalchemy.orm import Session

from app.models.enums import ChannelType, NotificationStatus
from app.models.mixins import utcnow
from app.models.notification import NotificationChannel, NotificationLog
from app.services.notification.dispatcher import SENDERS

logger = logging.getLogger(__name__)

# Total sends per notification, the first attempt included. Four retries
# spread over roughly forty minutes rides out the outages that actually
# happen -- a provider blip, an SMTP timeout, a phone off a network -- without
# pretending a revoked bot token will heal.
MAX_ATTEMPTS = 5

# Seconds to wait before attempt N+1. Growing, because the failures worth
# retrying clear in seconds while the ones that do not clear at all would
# otherwise be hammered every poll for as long as the process lives.
_BACKOFF_SEC = (30, 120, 480, 1800)

# Not swept forever: a row this old has been failing across the whole backoff
# ladder and then some, and re-sending an order notification from yesterday
# would tell the owner about something that has already expired.
_MAX_AGE = timedelta(hours=6)

# One sweep should not turn into a long blocking loop inside the market tick,
# which is the same thread that checks stop-losses.
_MAX_PER_SWEEP = 20


def schedule_first_retry(log: NotificationLog) -> None:
    """Called by the dispatcher when a first delivery fails."""
    log.attempts = 1
    log.next_retry_at = utcnow() + timedelta(seconds=_BACKOFF_SEC[0])


def _next_due(attempts: int):
    """None once the ladder is exhausted -- the row then stays FAILED with
    nothing owed, which is what makes a broken channel visible rather than an
    endless queue."""
    if attempts >= MAX_ATTEMPTS:
        return None
    return utcnow() + timedelta(seconds=_BACKOFF_SEC[min(attempts - 1, len(_BACKOFF_SEC) - 1)])


def retry_pending(db: Session) -> int:
    """Send everything that is due. Returns how many got through this sweep."""
    now = utcnow()
    due = (
        db.query(NotificationLog)
        .filter(
            NotificationLog.status == NotificationStatus.FAILED,
            NotificationLog.next_retry_at.isnot(None),
            NotificationLog.next_retry_at <= now,
        )
        .order_by(NotificationLog.next_retry_at)
        .limit(_MAX_PER_SWEEP)
        .all()
    )

    delivered = 0
    for log in due:
        if not _still_worth_sending(db, log, now):
            continue

        channel = db.get(NotificationChannel, log.channel_id)
        sender = SENDERS.get(channel.channel_type) if channel else None
        if sender is None:
            log.next_retry_at = None
            db.commit()
            continue

        log.attempts += 1
        try:
            result = sender.send(channel.config_encrypted, log.message)
        except Exception as exc:
            logger.exception("notification retry crashed for channel %s", channel.id)
            ok, error = False, str(exc)
        else:
            ok, error = result.ok, result.error

        if ok:
            log.status = NotificationStatus.SENT
            log.error = None
            log.next_retry_at = None
            channel.last_error = None
            delivered += 1
            logger.info(
                "notification %s delivered on attempt %s (channel %s)",
                log.id,
                log.attempts,
                channel.id,
            )
        elif _is_permanent(error):
            # Retrying cannot help and the owner cannot see the problem: the
            # channel would go on saying 啟用中 while every event posted into
            # a void. Switching it off is what makes the silence legible, and
            # the message has to name the fix, because "410 Gone" tells them
            # nothing about re-subscribing.
            log.error = error
            log.next_retry_at = None
            channel.is_enabled = False
            channel.last_error = _permanent_explanation(channel.channel_type, error)
            logger.warning("disabling channel %s: %s is permanent", channel.id, error)
        else:
            log.error = error
            log.next_retry_at = _next_due(log.attempts)
            channel.last_error = error
            if log.next_retry_at is None:
                logger.warning(
                    "giving up on notification %s after %s attempts (channel %s): %s",
                    log.id,
                    log.attempts,
                    channel.id,
                    error,
                )
        channel.last_sent_at = utcnow()
        db.commit()

    return delivered


def _still_worth_sending(db: Session, log: NotificationLog, now) -> bool:
    """Three ways a queued retry stops being owed. Each one clears the due
    time so the sweep does not keep picking the row up."""
    if log.attempts >= MAX_ATTEMPTS or not log.message:
        # No message means a row written before retries existed: there is
        # nothing to resend, and making something up is worse than silence.
        log.next_retry_at = None
        db.commit()
        return False

    # created_at is naive out of SQLite, so compare on the same footing.
    age = now.replace(tzinfo=None) - log.created_at.replace(tzinfo=None)
    if age > _MAX_AGE:
        log.next_retry_at = None
        db.commit()
        logger.warning("notification %s expired unsent after %s", log.id, age)
        return False

    channel = db.get(NotificationChannel, log.channel_id)
    if channel is None or not channel.is_enabled:
        # The owner turned it off, or deleted it. Either way they have stopped
        # asking to be told this way.
        log.next_retry_at = None
        db.commit()
        return False

    return True


# Codes that mean "this will never work again", as opposed to the timeouts and
# 5xx that clear on their own. Matched on the "HTTP <code>" prefix every sender
# now emits.
#
# THE SPLIT MATTERS, and getting it wrong destroyed working subscriptions. For
# web push, 404 and 410 mean the DEVICE's subscription is gone -- recreating it
# on the phone is exactly the right advice. But Apple answers 403
# (VapidPkHashMismatch) and 400 (BadJwtToken) when the credentials THIS SERVER
# signed with are wrong: a key pair that no longer matches, a malformed
# subject. Both were lumped together, so a server-side misconfiguration
# switched the channel off and told the owner to delete and re-create it on
# their phone -- which cannot help, and throws away the one working part while
# they try.
_DEVICE_GONE_CODES = ("HTTP 404", "HTTP 410")
# Wrong credentials on our side. Retrying cannot fix them either, so the
# channel still stops; only the explanation differs.
_SERVER_FAULT_CODES = ("HTTP 400", "HTTP 401", "HTTP 403")
# The payload was too big for the push service. webpush.MAX_BODY_CHARS is the
# belt; this is the braces, so an oversized alert is not retried five times in
# silence and then dropped.
_TOO_LARGE_CODES = ("HTTP 413",)

_PERMANENT_CODES = _DEVICE_GONE_CODES + _SERVER_FAULT_CODES + _TOO_LARGE_CODES


def _is_permanent(error: str | None) -> bool:
    return bool(error) and error.startswith(_PERMANENT_CODES)


def _permanent_explanation(channel_type: ChannelType, error: str) -> str:
    """What the owner should actually do. A raw status code is not that."""
    if channel_type == ChannelType.WEB_PUSH:
        if error.startswith(_SERVER_FAULT_CODES):
            return (
                f"推播服務拒絕了伺服器的憑證（{error}），這個管道已自動停用。"
                "這不是你的裝置的問題，重新建立推播管道也不會好 —— "
                "是伺服器上的 VAPID 金鑰設定有問題（多半是 VAPID_PUBLIC_KEY 與 "
                "VAPID_PRIVATE_KEY 不是同一對，或 VAPID_SUBJECT 不是合法的 "
                "mailto: 位址）。請先修正伺服器設定，再重新啟用這個管道。"
            )
        if error.startswith(_TOO_LARGE_CODES):
            return (
                f"這則通知的內容太長，推播服務不收（{error}），這個管道已自動停用。"
                "內容長度已經有上限保護，會出現這個錯誤通常代表推播服務的限制改了。"
                "重新啟用即可，若持續發生請回報。"
            )
        return (
            f"瀏覽器的推播訂閱已失效（{error}），這個管道已自動停用。"
            "請在這台裝置上刪除後重新建立推播管道。"
        )
    if channel_type == ChannelType.TELEGRAM:
        return f"Telegram 拒絕了這個管道（{error}），已自動停用。請確認機器人權杖與聊天室代號。"
    if channel_type == ChannelType.LINE:
        return f"LINE 拒絕了這個管道（{error}），已自動停用。請確認存取權杖。"
    return f"這個管道被對方拒絕（{error}），已自動停用。請重新設定後再啟用。"
