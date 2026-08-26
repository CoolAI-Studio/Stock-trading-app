"""「失敗」 on the history page was four different situations wearing one word.

A NotificationLog row with status FAILED can mean any of:

  it is being held for quiet hours and will go out at 07:00;
  the third of five attempts just failed and the fourth is due in eight
    minutes;
  all five attempts are spent and nothing more will be tried;
  the channel was switched off or deleted, so nothing more will be tried.

The API returned `status` and `error` and nothing else, so the page printed
「失敗」 for all four and the raw provider message underneath. For the owner of
an alerting app the only question that matters about a failed alert is 「so is
it still coming or not?」, and that was the one thing the answer did not
contain.

The distinction lives in `attempts` and `next_retry_at`, which the table has
always carried and the API never exposed. Rather than ship two raw columns and
let the page re-derive the rules -- backoff ladder, MAX_ATTEMPTS, the several
paths that clear a due time -- the model answers the question itself, so there
is exactly one definition of 「still coming」 and the retry sweep and the screen
cannot drift apart.

NULL next_retry_at is the whole of 「nothing more will be tried」, deliberately.
It is reached by giving up, by the channel being disabled, by the row expiring
past _MAX_AGE, and by a notice that was never retryable to begin with. Those
are four causes of one fact, and the owner needs the fact.
"""

from datetime import timedelta

from app.enums import ChannelType, NotificationStatus
from app.models.mixins import utcnow
from app.models.notification import NotificationChannel, NotificationLog
from app.models.user import User


def _user(db_session) -> User:
    user = User(email="state@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _log(db_session, user, **kw) -> NotificationLog:
    kw.setdefault("status", NotificationStatus.FAILED)
    row = NotificationLog(
        user_id=user.id,
        event="order.created",
        message="有新的待確認訂單",
        **kw,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


# --- the four situations ----------------------------------------------------


def test_a_sent_notification_says_so(db_session):
    user = _user(db_session)
    row = _log(db_session, user, status=NotificationStatus.SENT, attempts=1, next_retry_at=None)

    assert row.delivery_state == "sent"


def test_a_notification_waiting_out_quiet_hours_is_not_a_failure(db_session):
    """It has not been attempted at all -- attempts is 0 and the due time is
    the end of the window the owner chose. Calling that 「失敗」 is how somebody
    concludes their quiet hours are dropping alerts and switches them off."""
    user = _user(db_session)
    row = _log(
        db_session, user, attempts=0, next_retry_at=utcnow() + timedelta(hours=6), error="靜音時段"
    )

    assert row.delivery_state == "deferred"


def test_a_notification_between_attempts_says_it_is_still_coming(db_session):
    user = _user(db_session)
    row = _log(db_session, user, attempts=3, next_retry_at=utcnow() + timedelta(minutes=8))

    assert row.delivery_state == "retrying"


def test_a_notification_with_nothing_owed_says_it_has_stopped(db_session):
    """The five attempts are spent. Nothing will happen next, and the owner
    has to be able to tell that from a row that is merely between tries."""
    user = _user(db_session)
    row = _log(db_session, user, attempts=5, next_retry_at=None)

    assert row.delivery_state == "given_up"


def test_a_channel_the_owner_switched_off_reads_the_same_way(db_session):
    """A different cause, the same fact: nothing more will be tried. The owner
    needs the fact."""
    user = _user(db_session)
    row = _log(db_session, user, attempts=2, next_retry_at=None)

    assert row.delivery_state == "given_up"


def test_a_row_that_reached_nobody_has_stopped_too(db_session):
    """channel_id NULL, attempts 0, no due time -- there was never anywhere to
    send it. 「還在重試」 would be a lie in the most important direction."""
    user = _user(db_session)
    row = _log(db_session, user, channel_id=None, attempts=0, next_retry_at=None)

    assert row.delivery_state == "given_up"


# --- it reaches the screen ---------------------------------------------------


def _channel(db_session, user_id) -> NotificationChannel:
    channel = NotificationChannel(
        user_id=user_id,
        channel_type=ChannelType.TELEGRAM,
        label="phone",
        config_encrypted={"bot_token": "t", "chat_id": "1"},
        is_enabled=True,
    )
    db_session.add(channel)
    db_session.commit()
    db_session.refresh(channel)
    return channel


def test_the_api_carries_the_state(auth_client, db_session):
    user = db_session.query(User).first()
    channel = _channel(db_session, user.id)
    _log(
        db_session,
        user,
        channel_id=channel.id,
        attempts=3,
        next_retry_at=utcnow() + timedelta(minutes=8),
    )

    row = auth_client.get("/api/notifications/logs").json()[0]

    assert row["delivery_state"] == "retrying"


def test_the_api_says_when_the_next_attempt_is_due(auth_client, db_session):
    """「還在重試」 without a time is barely better than 「失敗」. The owner is
    deciding whether to go and look at their broker app right now."""
    user = db_session.query(User).first()
    channel = _channel(db_session, user.id)
    _log(
        db_session,
        user,
        channel_id=channel.id,
        attempts=3,
        next_retry_at=utcnow() + timedelta(minutes=8),
    )

    row = auth_client.get("/api/notifications/logs").json()[0]

    assert row["next_retry_at"] is not None
    assert row["attempts"] == 3


def test_the_api_reports_how_many_attempts_are_possible_at_all(auth_client, db_session):
    """「第 3 次」 means nothing without 「共 5 次」, and hard-coding 5 into the
    page is a second definition of the ladder waiting to drift."""
    user = db_session.query(User).first()
    channel = _channel(db_session, user.id)
    _log(db_session, user, channel_id=channel.id, attempts=3, next_retry_at=utcnow())

    row = auth_client.get("/api/notifications/logs").json()[0]

    from app.services.notification.retry import MAX_ATTEMPTS

    assert row["max_attempts"] == MAX_ATTEMPTS


def test_a_given_up_row_is_visibly_different_through_the_api(auth_client, db_session):
    user = db_session.query(User).first()
    channel = _channel(db_session, user.id)
    _log(db_session, user, channel_id=channel.id, attempts=5, next_retry_at=None)

    row = auth_client.get("/api/notifications/logs").json()[0]

    assert row["delivery_state"] == "given_up"
    assert row["next_retry_at"] is None
