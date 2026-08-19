"""A channel that will never work again has to say so and get out of the way.

Browsers rotate push subscriptions -- mobile Chrome and Edge do it on their
own schedule, without telling anyone. Once rotated, the old endpoint returns
410 Gone forever. The channel went on showing 啟用中, every event went on
posting to a dead endpoint and writing a failed row, and the owner's phone
simply never rang again. The only recovery anyone would ever find is deleting
the channel and creating a new one, which nothing on screen suggests.

The same shape covers a revoked Telegram bot token (401) and a bad chat id
(400 chat not found): permanent, self-evident from the response, and worth
distinguishing from the timeout that clears in ten seconds.
"""

from unittest.mock import patch

from app.models.enums import ChannelType, NotificationStatus
from app.models.mixins import utcnow
from app.models.notification import NotificationChannel, NotificationLog
from app.models.user import User
from app.services.notification import retry
from app.services.notification.base import SendResult


def _user(db_session) -> User:
    user = User(email="dead@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _channel(db_session, user, channel_type=ChannelType.WEB_PUSH) -> NotificationChannel:
    channel = NotificationChannel(
        user_id=user.id,
        channel_type=channel_type,
        label="phone",
        config_encrypted={"endpoint": "https://push.example/x", "p256dh": "k", "auth": "a"},
        is_enabled=True,
    )
    db_session.add(channel)
    db_session.commit()
    db_session.refresh(channel)
    return channel


def _queued(db_session, user, channel) -> NotificationLog:
    log = NotificationLog(
        user_id=user.id,
        channel_id=channel.id,
        event="order.created",
        status=NotificationStatus.FAILED,
        error="gone",
        message="有新的待確認訂單",
        attempts=1,
        next_retry_at=utcnow(),
    )
    db_session.add(log)
    db_session.commit()
    db_session.refresh(log)
    return log


def test_a_gone_subscription_disables_the_channel_instead_of_retrying_it(db_session):
    user = _user(db_session)
    channel = _channel(db_session, user)
    log = _queued(db_session, user, channel)

    with patch.object(
        retry.SENDERS[ChannelType.WEB_PUSH],
        "send",
        return_value=SendResult(ok=False, error="HTTP 410: push subscription has unsubscribed"),
    ):
        retry.retry_pending(db_session)

    db_session.refresh(channel)
    db_session.refresh(log)
    assert channel.is_enabled is False, "410 is permanent; leaving it on means silent failure"
    assert log.next_retry_at is None, "no amount of retrying revives a gone endpoint"
    assert channel.last_error and "重新" in channel.last_error, (
        "the owner has to be told the fix is to re-subscribe, not just that it broke"
    )


def test_a_revoked_telegram_token_disables_the_channel_too(db_session):
    user = _user(db_session)
    channel = _channel(db_session, user, ChannelType.TELEGRAM)
    log = _queued(db_session, user, channel)

    with patch.object(
        retry.SENDERS[ChannelType.TELEGRAM],
        "send",
        return_value=SendResult(ok=False, error="HTTP 401: Unauthorized"),
    ):
        retry.retry_pending(db_session)

    db_session.refresh(channel)
    db_session.refresh(log)
    assert channel.is_enabled is False
    assert log.next_retry_at is None


def test_a_timeout_is_not_treated_as_permanent(db_session):
    """The whole reason retries exist. Disabling a channel over one blip
    would turn a ten-second outage into a permanently silent phone."""
    user = _user(db_session)
    channel = _channel(db_session, user)
    log = _queued(db_session, user, channel)

    with patch.object(
        retry.SENDERS[ChannelType.WEB_PUSH],
        "send",
        return_value=SendResult(ok=False, error="Read timed out"),
    ):
        retry.retry_pending(db_session)

    db_session.refresh(channel)
    db_session.refresh(log)
    assert channel.is_enabled is True
    assert log.next_retry_at is not None


def test_a_server_error_is_not_treated_as_permanent(db_session):
    """503 is the push service having a bad afternoon, not the subscription
    being gone."""
    user = _user(db_session)
    channel = _channel(db_session, user)
    log = _queued(db_session, user, channel)

    with patch.object(
        retry.SENDERS[ChannelType.WEB_PUSH],
        "send",
        return_value=SendResult(ok=False, error="HTTP 503: Service Unavailable"),
    ):
        retry.retry_pending(db_session)

    db_session.refresh(channel)
    db_session.refresh(log)
    assert channel.is_enabled is True
    assert log.next_retry_at is not None, "a 5xx is worth trying again"


def test_the_push_sender_reports_the_status_code_it_got(db_session):
    """retry.py can only recognise a permanent failure if the sender puts the
    code in the message. WebPushSender used to return str(exc) alone, which
    for pywebpush is prose with no code in it."""
    from pywebpush import WebPushException

    from app.services.notification.webpush import WebPushSender

    class _Response:
        status_code = 410
        text = "push subscription has unsubscribed or expired"

    exc = WebPushException("failed", response=_Response())
    with (
        patch("app.services.notification.webpush.webpush", side_effect=exc),
        patch("app.config.settings.VAPID_PRIVATE_KEY", "x"),
    ):
        result = WebPushSender().send(
            {"endpoint": "https://push.example/x", "p256dh": "k", "auth": "a"}, "hi"
        )

    assert result.ok is False
    assert "410" in result.error
