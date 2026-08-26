"""Not being woken at 3am, without switching the alerts off.

US market hours are the middle of the night in Taipei. A strategy firing at
03:00 makes the phone ring, and the only control the owner had was disabling
the whole channel -- which takes the stop-loss alerts with it. That is this
product's critical failure arrived at through the front door: the owner turns
the warnings off themselves, because the warnings are unusable.

So quiet hours **defer rather than drop**. A notification raised inside the
window is held and delivered when the window ends, reusing the retry queue
that already exists for failed deliveries. Dropping it would be the same
silence, just chosen by us instead of by them.
"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.enums import ChannelType, NotificationStatus, OrderSide, OrderSource, OrderStatus
from app.models.notification import NotificationChannel, NotificationLog
from app.models.order import Order
from app.models.user import User
from app.services.notification import quiet_hours
from app.services.notification.base import SendResult

TPE = ZoneInfo("Asia/Taipei")


def _user(db_session, timezone: str = "Asia/Taipei") -> User:
    user = User(email="quiet@example.com", hashed_password="x", timezone=timezone)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _channel(db_session, user, **kw) -> NotificationChannel:
    channel = NotificationChannel(
        user_id=user.id,
        channel_type=ChannelType.TELEGRAM,
        label="phone",
        config_encrypted={"bot_token": "t", "chat_id": "1"},
        is_enabled=True,
        **kw,
    )
    db_session.add(channel)
    db_session.commit()
    db_session.refresh(channel)
    return channel


# --- the window itself ------------------------------------------------------


def test_a_channel_with_no_window_is_never_quiet():
    middle_of_the_night = datetime(2026, 8, 19, 19, 0, tzinfo=UTC)
    assert not quiet_hours.is_quiet(None, None, "Asia/Taipei", middle_of_the_night)


def test_an_overnight_window_wraps_past_midnight():
    """23:00 to 07:00 is the ordinary case and the one a naive start <= hour
    < end comparison gets exactly backwards."""
    at_3am = datetime(2026, 8, 19, 3, 0, tzinfo=TPE)
    at_noon = datetime(2026, 8, 19, 12, 0, tzinfo=TPE)

    assert quiet_hours.is_quiet(23, 7, "Asia/Taipei", at_3am)
    assert not quiet_hours.is_quiet(23, 7, "Asia/Taipei", at_noon)


def test_a_same_day_window_works_too():
    at_2pm = datetime(2026, 8, 19, 14, 0, tzinfo=TPE)
    assert quiet_hours.is_quiet(13, 16, "Asia/Taipei", at_2pm)
    assert not quiet_hours.is_quiet(13, 16, "Asia/Taipei", datetime(2026, 8, 19, 17, 0, tzinfo=TPE))


def test_the_window_is_read_in_the_owners_timezone_not_the_servers():
    """The container runs in UTC. 19:00 UTC is 03:00 in Taipei, which is
    inside a 23:00-07:00 window; reading the hour off UTC would call it 7pm
    and let the phone ring."""
    assert quiet_hours.is_quiet(23, 7, "Asia/Taipei", datetime(2026, 8, 19, 19, 0, tzinfo=UTC))


def test_an_unknown_timezone_falls_back_rather_than_raising():
    """A bad value in the column must not take the notification path down --
    the failure mode would be no alerts at all."""
    assert quiet_hours.is_quiet(23, 7, "Not/AZone", datetime(2026, 8, 19, 19, 0, tzinfo=UTC)) in (
        True,
        False,
    )


def test_when_the_window_ends_is_the_next_time_it_opens_up():
    inside = datetime(2026, 8, 19, 3, 0, tzinfo=TPE)
    ends = quiet_hours.window_ends_at(23, 7, "Asia/Taipei", inside)
    assert ends.astimezone(TPE).hour == 7
    assert ends > inside


def test_the_end_of_an_evening_window_is_tomorrow_morning():
    inside = datetime(2026, 8, 19, 23, 30, tzinfo=TPE)
    ends = quiet_hours.window_ends_at(23, 7, "Asia/Taipei", inside)
    local = ends.astimezone(TPE)
    assert local.hour == 7
    assert local.day == 20


# --- what the dispatcher does with it --------------------------------------


def _order(db_session, user) -> Order:
    order = Order(
        user_id=user.id,
        source=OrderSource.MANUAL,
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal(1),
        status=OrderStatus.PENDING,
    )
    db_session.add(order)
    db_session.commit()
    db_session.refresh(order)
    return order


def _dispatch_at(db_session, user, moment: datetime):
    from app.services.events import Event
    from app.services.notification import dispatcher

    with patch("app.services.notification.quiet_hours.utcnow", return_value=moment):
        return dispatcher.handle_event(
            Event(type="order.created", data={"user_id": user.id, "order_id": None}),
            db=db_session,
        )


def test_a_notification_inside_the_window_is_held_rather_than_sent(db_session):
    user = _user(db_session)
    channel = _channel(db_session, user, quiet_start_hour=23, quiet_end_hour=7)

    with patch.object(
        __import__("app.services.notification.dispatcher", fromlist=["SENDERS"]).SENDERS[
            ChannelType.TELEGRAM
        ],
        "send",
        return_value=SendResult(ok=True),
    ) as send:
        _dispatch_at(db_session, user, datetime(2026, 8, 19, 19, 0, tzinfo=UTC))  # 03:00 Taipei

    assert send.call_count == 0, "the phone must not ring at three in the morning"
    log = db_session.query(NotificationLog).filter(NotificationLog.channel_id == channel.id).one()
    assert log.status == NotificationStatus.FAILED
    assert log.next_retry_at is not None, "held, not dropped"
    assert "靜音" in (log.error or "")


def test_the_held_notification_is_due_when_the_window_ends(db_session):
    user = _user(db_session)
    channel = _channel(db_session, user, quiet_start_hour=23, quiet_end_hour=7)

    with patch.object(
        __import__("app.services.notification.dispatcher", fromlist=["SENDERS"]).SENDERS[
            ChannelType.TELEGRAM
        ],
        "send",
        return_value=SendResult(ok=True),
    ):
        _dispatch_at(db_session, user, datetime(2026, 8, 19, 19, 0, tzinfo=UTC))

    log = db_session.query(NotificationLog).filter(NotificationLog.channel_id == channel.id).one()
    # SQLite hands the column back naive; it was stored as UTC, like every
    # other timestamp here.
    due = log.next_retry_at.replace(tzinfo=UTC)
    assert due.astimezone(TPE).hour == 7


def test_outside_the_window_it_goes_straight_out(db_session):
    user = _user(db_session)
    channel = _channel(db_session, user, quiet_start_hour=23, quiet_end_hour=7)

    with patch.object(
        __import__("app.services.notification.dispatcher", fromlist=["SENDERS"]).SENDERS[
            ChannelType.TELEGRAM
        ],
        "send",
        return_value=SendResult(ok=True),
    ) as send:
        _dispatch_at(db_session, user, datetime(2026, 8, 19, 4, 0, tzinfo=UTC))  # noon in Taipei

    assert send.call_count == 1
    log = db_session.query(NotificationLog).filter(NotificationLog.channel_id == channel.id).one()
    assert log.status == NotificationStatus.SENT


def test_a_channel_without_quiet_hours_is_unaffected(db_session):
    user = _user(db_session)
    channel = _channel(db_session, user)

    with patch.object(
        __import__("app.services.notification.dispatcher", fromlist=["SENDERS"]).SENDERS[
            ChannelType.TELEGRAM
        ],
        "send",
        return_value=SendResult(ok=True),
    ) as send:
        _dispatch_at(db_session, user, datetime(2026, 8, 19, 19, 0, tzinfo=UTC))

    assert send.call_count == 1
    log = db_session.query(NotificationLog).filter(NotificationLog.channel_id == channel.id).one()
    assert log.status == NotificationStatus.SENT
