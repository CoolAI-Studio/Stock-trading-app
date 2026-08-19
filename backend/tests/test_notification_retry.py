"""A notification that failed to send has to be tried again.

This product's whole job is to tell the owner something happened. An alert
that does not arrive is its one unaffordable failure -- worse than a missing
order type, worse than a wrong number on a page, because the owner cannot
even tell it happened.

Alert-only strategies already survive a blip, but only by accident of shape:
the strategy re-fires every tick, so the *next* signal retries the delivery.
Order and strategy-error notifications fire once. A ten-second Telegram
outage, one SMTP timeout, and the pending-order notice was gone for good --
the two events that most need to arrive were the two with no second chance.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from app.models.enums import ChannelType, NotificationStatus, OrderSide, OrderSource, OrderStatus
from app.models.mixins import utcnow
from app.models.notification import NotificationChannel, NotificationLog
from app.models.order import Order
from app.models.user import User
from app.services.notification import retry
from app.services.notification.base import SendResult

MESSAGE = "有新的待確認訂單：AAPL 買進 10"


def _user(db_session) -> User:
    user = User(email="retry@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _channel(db_session, user: User) -> NotificationChannel:
    channel = NotificationChannel(
        user_id=user.id,
        channel_type=ChannelType.TELEGRAM,
        label="phone",
        config_encrypted={"bot_token": "t", "chat_id": "1"},
        is_enabled=True,
    )
    db_session.add(channel)
    db_session.commit()
    db_session.refresh(channel)
    return channel


def _failed_log(db_session, user, channel, *, due_in_sec: int = -1, attempts: int = 1):
    log = NotificationLog(
        user_id=user.id,
        channel_id=channel.id,
        event="order.created",
        status=NotificationStatus.FAILED,
        error="Telegram timed out",
        message=MESSAGE,
        attempts=attempts,
        next_retry_at=utcnow() + timedelta(seconds=due_in_sec),
    )
    db_session.add(log)
    db_session.commit()
    db_session.refresh(log)
    return log


def _sending(result: SendResult):
    return patch.object(retry.SENDERS[ChannelType.TELEGRAM], "send", return_value=result)


def _seconds_until(when) -> float:
    # next_retry_at comes back naive from SQLite, so compare like with like.
    return (when.replace(tzinfo=None) - utcnow().replace(tzinfo=None)).total_seconds()


def test_a_failed_notification_is_sent_again_when_it_comes_due(db_session):
    user = _user(db_session)
    channel = _channel(db_session, user)
    log = _failed_log(db_session, user, channel)

    with _sending(SendResult(ok=True)):
        retry.retry_pending(db_session)

    db_session.refresh(log)
    assert log.status == NotificationStatus.SENT
    assert log.next_retry_at is None, "delivered, so nothing more is owed"
    assert log.attempts == 2


def test_the_original_message_is_what_gets_resent(db_session):
    """Not a placeholder: the event that produced it is long gone by the time
    the retry runs, so the rendered text has to have been kept."""
    user = _user(db_session)
    channel = _channel(db_session, user)
    _failed_log(db_session, user, channel)

    with _sending(SendResult(ok=True)) as send:
        retry.retry_pending(db_session)

    assert send.call_args.args[1] == MESSAGE


def test_a_notification_not_yet_due_is_left_alone(db_session):
    """The backoff is the whole point -- retrying every poll would hammer a
    dead endpoint five times a minute."""
    user = _user(db_session)
    channel = _channel(db_session, user)
    _failed_log(db_session, user, channel, due_in_sec=300)

    with _sending(SendResult(ok=True)) as send:
        retry.retry_pending(db_session)

    assert send.call_count == 0


def test_each_failure_pushes_the_next_attempt_further_out(db_session):
    user = _user(db_session)
    channel = _channel(db_session, user)
    log = _failed_log(db_session, user, channel, attempts=1)

    with _sending(SendResult(ok=False, error="503")):
        retry.retry_pending(db_session)

    db_session.refresh(log)
    assert log.attempts == 2
    assert log.status == NotificationStatus.FAILED
    first_gap = _seconds_until(log.next_retry_at)

    log.next_retry_at = utcnow() - timedelta(seconds=1)
    db_session.commit()
    with _sending(SendResult(ok=False, error="503")):
        retry.retry_pending(db_session)

    db_session.refresh(log)
    assert log.attempts == 3
    assert _seconds_until(log.next_retry_at) > first_gap, (
        "backoff has to grow, or a dead channel is polled forever"
    )


def test_retrying_stops_after_the_bound_rather_than_forever(db_session):
    """A revoked bot token never recovers on its own. Past the bound the row
    stays FAILED with no next attempt, which is also what makes it visible as
    a broken channel rather than an endless queue."""
    user = _user(db_session)
    channel = _channel(db_session, user)
    log = _failed_log(db_session, user, channel, attempts=retry.MAX_ATTEMPTS)

    with _sending(SendResult(ok=False, error="401")) as send:
        retry.retry_pending(db_session)

    db_session.refresh(log)
    assert send.call_count == 0, "already at the bound"
    assert log.next_retry_at is None
    assert log.status == NotificationStatus.FAILED


def test_a_disabled_channel_is_not_retried(db_session):
    user = _user(db_session)
    channel = _channel(db_session, user)
    log = _failed_log(db_session, user, channel)
    channel.is_enabled = False
    db_session.commit()

    with _sending(SendResult(ok=True)) as send:
        retry.retry_pending(db_session)

    db_session.refresh(log)
    assert send.call_count == 0
    assert log.next_retry_at is None, "the owner switched it off; stop owing them this"


def test_a_row_from_before_the_message_was_recorded_is_not_retried(db_session):
    """Existing rows have no message to resend. Skipping them beats inventing
    text and sending the owner something that never happened."""
    user = _user(db_session)
    channel = _channel(db_session, user)
    log = _failed_log(db_session, user, channel)
    log.message = None
    db_session.commit()

    with _sending(SendResult(ok=True)) as send:
        retry.retry_pending(db_session)

    assert send.call_count == 0
    db_session.refresh(log)
    assert log.next_retry_at is None


def test_a_delivery_failure_is_queued_for_retry_when_it_first_happens(db_session):
    """The dispatcher is where the queue starts: without it setting a due
    time, nothing the sweep looks for ever exists."""
    from app.services.events import Event
    from app.services.notification import dispatcher

    user = _user(db_session)
    channel = _channel(db_session, user)
    order = Order(
        user_id=user.id,
        source=OrderSource.MANUAL,
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal(10),
        status=OrderStatus.PENDING,
    )
    db_session.add(order)
    db_session.commit()

    with patch.object(
        dispatcher.SENDERS[ChannelType.TELEGRAM],
        "send",
        return_value=SendResult(ok=False, error="timed out"),
    ):
        dispatcher.handle_event(
            Event(type="order.created", data={"user_id": user.id, "order_id": order.id}),
            db=db_session,
        )

    log = db_session.query(NotificationLog).filter(NotificationLog.channel_id == channel.id).one()
    assert log.status == NotificationStatus.FAILED
    assert log.next_retry_at is not None, "a failure with no due time is a failure nobody retries"
    assert log.message, "the rendered text has to be kept or there is nothing to resend"
    assert log.attempts == 1


def test_a_delivered_notification_is_not_queued(db_session):
    from app.services.events import Event
    from app.services.notification import dispatcher

    user = _user(db_session)
    channel = _channel(db_session, user)

    with patch.object(
        dispatcher.SENDERS[ChannelType.TELEGRAM], "send", return_value=SendResult(ok=True)
    ):
        dispatcher.handle_event(
            Event(type="order.created", data={"user_id": user.id, "order_id": None}), db=db_session
        )

    log = db_session.query(NotificationLog).filter(NotificationLog.channel_id == channel.id).one()
    assert log.status == NotificationStatus.SENT
    assert log.next_retry_at is None
