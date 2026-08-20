"""Two modules that contradicted each other, and the ledger flooding itself.

## The contradiction

quiet_hours.py's header says the alert is 「Deferred, never dropped」 -- held
during the window and delivered when it ends. dispatcher.py writes it with
next_retry_at set to the end of the window, exactly as promised.

Then retry.py's sweep picks it up and checks `age > _MAX_AGE` FIRST, where
_MAX_AGE is six hours. The UI's own default quiet window is 23:00 to 07:00 --
eight hours. So an alert raised at 23:05 is 7h55m old when its window ends, the
sweep discards it before sending, and the owner is never told.

The default configuration guaranteed that alerts raised while asleep were
thrown away. Both halves were individually reasonable: six hours is right for a
transient failure (a price alert from this morning is not worth sending at
lunchtime), and holding through the night is right for quiet hours. Together
they silently dropped exactly the alerts somebody sets quiet hours to receive
politely rather than not at all.

The fix measures a held alert's age from when it became DUE, not from when it
was raised. A deferral is not a failure and its wait is not staleness.

## The flood

Recording an alert that reached nobody (see test_alerts_reaching_nobody.py) is
right, but with `alert_interval_sec = 0` -- documented as "notify every time" --
and no channels, it wrote one row per poll. At the 5-second default that is
17,280 rows a day, on a free-tier database, burying the very rows it exists to
make visible.

Bounded to one row per window, with a count of how many were folded into it, so
the ledger stays both readable and honest about how many alerts were missed.
"""

from datetime import timedelta

from app.models.enums import ChannelType, NotificationStatus
from app.models.mixins import utcnow
from app.models.notification import NotificationChannel, NotificationLog
from app.services.events import Event
from app.services.notification import retry
from app.services.notification.dispatcher import handle_event


def _user_id(db_session) -> int:
    from app.models.user import User

    user = db_session.query(User).first()
    if user is None:
        user = User(email="quiet@example.com", hashed_password="x")
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    return user.id


def _held_log(db_session, user_id: int, channel_id: int, *, raised_ago, due_ago) -> NotificationLog:
    """An alert deferred for a quiet window that has now ended."""
    now = utcnow()
    log = NotificationLog(
        user_id=user_id,
        channel_id=channel_id,
        event="order.created",
        status=NotificationStatus.FAILED,
        error="靜音時段，將在 07:00 UTC 之後送出",
        message="2330.TW 跌破 950",
        attempts=0,
        next_retry_at=now - due_ago,
    )
    db_session.add(log)
    db_session.commit()
    # created_at defaults to now, so it is set explicitly afterwards.
    log.created_at = now - raised_ago
    db_session.commit()
    db_session.refresh(log)
    return log


def _channel(db_session, user_id: int) -> NotificationChannel:
    channel = NotificationChannel(
        user_id=user_id,
        channel_type=ChannelType.TELEGRAM,
        label="tg",
        config_encrypted={"bot_token": "t", "chat_id": "1"},
        is_enabled=True,
    )
    db_session.add(channel)
    db_session.commit()
    db_session.refresh(channel)
    return channel


# --- an alert held overnight still gets sent --------------------------------


def test_an_alert_held_through_an_eight_hour_night_is_still_delivered(db_session, monkeypatch):
    """The exact default case: 23:00-07:00 is eight hours, _MAX_AGE is six, and
    the sweep used to discard the alert before sending it."""
    user_id = _user_id(db_session)
    channel = _channel(db_session, user_id)
    log = _held_log(
        db_session,
        user_id,
        channel.id,
        raised_ago=timedelta(hours=7, minutes=55),
        due_ago=timedelta(seconds=1),
    )

    from app.services.notification.base import SendResult

    sent: list[str] = []
    monkeypatch.setattr(
        "app.services.notification.telegram.TelegramSender.send",
        lambda self, config, message: (sent.append(message), SendResult(ok=True))[1],
    )

    retry.retry_pending(db_session)

    db_session.refresh(log)
    assert sent == ["2330.TW 跌破 950"]
    assert log.status == NotificationStatus.SENT


def test_a_genuinely_stale_retry_is_still_given_up_on(db_session, monkeypatch):
    """The six-hour rule exists for a reason: a price alert that failed this
    morning is not worth sending at lunchtime. Held alerts are the exception,
    not the abolition of the rule."""
    user_id = _user_id(db_session)
    channel = _channel(db_session, user_id)
    # Raised seven hours ago AND due for the last seven hours -- nothing held
    # it, it has simply been failing.
    log = _held_log(
        db_session,
        user_id,
        channel.id,
        raised_ago=timedelta(hours=7),
        due_ago=timedelta(hours=7),
    )
    log.error = "HTTP 500"
    db_session.commit()

    sent: list[str] = []
    monkeypatch.setattr(
        "app.services.notification.telegram.TelegramSender.send",
        lambda self, config, message: sent.append(message),
    )

    retry.retry_pending(db_session)

    db_session.refresh(log)
    assert sent == []
    assert log.next_retry_at is None


def test_the_clock_starts_when_it_became_due_not_when_it_was_raised(db_session, monkeypatch):
    """A deferral is not a failure, and waiting out a window the owner asked
    for is not staleness."""
    user_id = _user_id(db_session)
    channel = _channel(db_session, user_id)
    # Raised 10 hours ago, due only a minute ago: it was held, not failing.
    log = _held_log(
        db_session,
        user_id,
        channel.id,
        raised_ago=timedelta(hours=10),
        due_ago=timedelta(minutes=1),
    )

    from app.services.notification.base import SendResult

    monkeypatch.setattr(
        "app.services.notification.telegram.TelegramSender.send",
        lambda self, config, message: SendResult(ok=True),
    )
    retry.retry_pending(db_session)

    db_session.refresh(log)
    assert log.status == NotificationStatus.SENT


# --- the ledger must not flood ----------------------------------------------


def _nobody_rows(db_session) -> list[NotificationLog]:
    return db_session.query(NotificationLog).filter(NotificationLog.channel_id.is_(None)).all()


def test_repeated_unreachable_alerts_collapse_into_one_row(db_session):
    """alert_interval_sec = 0 means "notify every time", and with no channels
    that wrote one row per five-second poll -- 17,280 a day, burying the row it
    exists to make visible."""
    user_id = _user_id(db_session)

    for _ in range(50):
        handle_event(Event(type="order.created", data={"user_id": user_id}), db=db_session)

    assert len(_nobody_rows(db_session)) == 1


def test_the_single_row_says_how_many_were_missed(db_session):
    """Collapsing must not lose the count: "one alert missed" and "fifty alerts
    missed" are very different things to read."""
    user_id = _user_id(db_session)

    for _ in range(5):
        handle_event(Event(type="order.created", data={"user_id": user_id}), db=db_session)

    row = _nobody_rows(db_session)[0]
    assert row.attempts == 5, "the fold count lives on attempts"
    assert "5" in (row.error or ""), row.error


def test_a_new_row_starts_once_the_window_has_passed(db_session):
    """Otherwise a single early row absorbs every later miss forever, and the
    ledger stops reflecting what is happening now."""
    user_id = _user_id(db_session)
    handle_event(Event(type="order.created", data={"user_id": user_id}), db=db_session)

    old = _nobody_rows(db_session)[0]
    old.created_at = utcnow() - timedelta(hours=2)
    db_session.commit()

    handle_event(Event(type="order.created", data={"user_id": user_id}), db=db_session)

    assert len(_nobody_rows(db_session)) == 2


def test_one_owners_flood_does_not_swallow_anothers_first_miss(db_session):
    from app.models.user import User

    first = _user_id(db_session)
    second = User(email="other@example.com", hashed_password="x")
    db_session.add(second)
    db_session.commit()
    db_session.refresh(second)

    handle_event(Event(type="order.created", data={"user_id": first}), db=db_session)
    handle_event(Event(type="order.created", data={"user_id": second.id}), db=db_session)

    assert len(_nobody_rows(db_session)) == 2
