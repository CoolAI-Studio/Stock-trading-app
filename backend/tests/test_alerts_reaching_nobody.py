"""An alert the system raised and told nobody about.

This is the purest form of the failure CLAUDE.md puts above everything else:
「警告不能停擺」. Something crossed a threshold, the app decided it was worth
telling the owner, and then it told nobody -- and left no trace, so the 發送紀錄
ledger showed exactly what it shows on a quiet afternoon when nothing happened.
The two are indistinguishable, which means the owner cannot discover the
failure even by going and looking for it.

The code already knew. DispatchResult's own docstring says "delivered == 0 is a
failure even when nothing raised -- a user with no enabled channel, or one
whose only channel is filtered out by subscribed_events, was not notified."
Nothing acted on it: `if not channels: return result`.

TWO CAUSES, TWO MESSAGES, because they need different fixes:
  - no enabled channel at all -> go and set one up
  - channels exist but every one of them filters this event type out -> go and
    tick the event on one of them

A DEFERRAL IS NOT A FAILURE. An alert held for a quiet window has reached a
channel and is queued; the sweep delivers it when the window ends. Counting
that as "reached nobody" would cry wolf every night and train the owner to
ignore the one that matters.
"""

from app.enums import ChannelType, NotificationStatus
from app.models.notification import NotificationChannel, NotificationLog
from app.services.events import Event
from app.services.notification.dispatcher import handle_event


def _event(user_id: int, event_type: str = "order.created") -> Event:
    return Event(type=event_type, data={"user_id": user_id, "order_id": 1})


def _user_id(db_session) -> int:
    """The db_session fixture starts empty -- it is auth_client that registers
    somebody -- so make one rather than depending on fixture ordering."""
    from app.models.user import User

    user = db_session.query(User).first()
    if user is None:
        user = User(email="alerts@example.com", hashed_password="x")
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    return user.id


def _channel(db_session, user_id: int, **kw) -> NotificationChannel:
    defaults = dict(
        channel_type=ChannelType.TELEGRAM,
        label="tg",
        config_encrypted={"bot_token": "t", "chat_id": "1"},
        is_enabled=True,
    )
    defaults.update(kw)
    channel = NotificationChannel(user_id=user_id, **defaults)
    db_session.add(channel)
    db_session.commit()
    db_session.refresh(channel)
    return channel


def _nobody_rows(db_session) -> list[NotificationLog]:
    return db_session.query(NotificationLog).filter(NotificationLog.channel_id.is_(None)).all()


# --- nobody was told --------------------------------------------------------


def test_an_alert_with_no_channels_at_all_is_recorded(db_session):
    """It used to return silently. The ledger then looked identical to an
    afternoon when nothing happened."""
    user_id = _user_id(db_session)

    result = handle_event(_event(user_id), db=db_session)

    assert result.delivered == 0
    rows = _nobody_rows(db_session)
    assert len(rows) == 1
    assert rows[0].status == NotificationStatus.FAILED


def test_the_record_says_what_to_go_and_do(db_session):
    user_id = _user_id(db_session)

    handle_event(_event(user_id), db=db_session)

    error = _nobody_rows(db_session)[0].error or ""
    assert "管道" in error, error


def test_it_keeps_the_message_that_nobody_got(db_session):
    """Without it the row says only that something was missed, and the owner
    cannot tell whether it mattered."""
    user_id = _user_id(db_session)

    handle_event(_event(user_id), db=db_session)

    assert _nobody_rows(db_session)[0].message


def test_it_records_which_event_went_unheard(db_session):
    user_id = _user_id(db_session)

    handle_event(_event(user_id, "strategy.error"), db=db_session)

    assert _nobody_rows(db_session)[0].event == "strategy.error"


def test_a_disabled_channel_is_the_same_as_no_channel(db_session):
    """Switching a channel off is how somebody stops the 3am buzzing. It is
    also how the alerts stop, and nothing said so."""
    user_id = _user_id(db_session)
    _channel(db_session, user_id, is_enabled=False)

    handle_event(_event(user_id), db=db_session)

    assert len(_nobody_rows(db_session)) == 1


def test_channels_that_all_filter_this_event_out_get_a_different_message(db_session):
    """A different cause needs a different fix: there IS a channel, it just
    does not want this event. Telling them to go and create a channel would
    send them the wrong way."""
    user_id = _user_id(db_session)
    _channel(db_session, user_id, subscribed_events=["something.else"])

    handle_event(_event(user_id), db=db_session)

    rows = _nobody_rows(db_session)
    assert len(rows) == 1
    assert "事件" in (rows[0].error or ""), rows[0].error


# --- do not cry wolf --------------------------------------------------------


def test_a_successful_send_records_nothing_extra(db_session, monkeypatch):
    user_id = _user_id(db_session)
    channel = _channel(db_session, user_id)

    from app.services.notification.base import SendResult

    monkeypatch.setattr(
        "app.services.notification.telegram.TelegramSender.send",
        lambda *a, **k: SendResult(ok=True),
    )
    handle_event(_event(user_id), db=db_session)

    assert _nobody_rows(db_session) == []
    assert db_session.query(NotificationLog).filter_by(channel_id=channel.id).count() == 1


def test_a_failed_send_is_not_reaching_nobody(db_session, monkeypatch):
    """It reached a channel and failed there -- that already has a row, and it
    is retried. A second row would double-count one alert."""
    user_id = _user_id(db_session)
    _channel(db_session, user_id)

    from app.services.notification.base import SendResult

    monkeypatch.setattr(
        "app.services.notification.telegram.TelegramSender.send",
        lambda *a, **k: SendResult(ok=False, error="boom"),
    )
    handle_event(_event(user_id), db=db_session)

    assert _nobody_rows(db_session) == []


def test_an_alert_held_for_quiet_hours_is_not_reaching_nobody(db_session):
    """Held is not lost: it reached a channel and is queued for when the window
    ends. Flagging it would cry wolf every night, and then the one that matters
    gets ignored too."""
    from app.models.mixins import utcnow
    from app.services.notification.quiet_hours import DEFAULT_TIMEZONE, _zone

    user_id = _user_id(db_session)
    # start == end reads as "no window" (quiet_hours.is_quiet), so the window
    # has to be built around whatever hour it happens to be when this runs --
    # a fixed pair would make the test pass or fail depending on the clock.
    now_hour = utcnow().astimezone(_zone(DEFAULT_TIMEZONE)).hour
    _channel(
        db_session,
        user_id,
        quiet_start_hour=now_hour,
        quiet_end_hour=(now_hour + 2) % 24,
    )

    result = handle_event(_event(user_id), db=db_session)

    assert result.deferred == 1
    assert _nobody_rows(db_session) == []


def test_an_event_type_that_never_notifies_records_nothing(db_session):
    """Only some event types are meant to notify. Writing a "nobody was told"
    row for the rest would fill the ledger with noise and bury the real ones."""
    user_id = _user_id(db_session)

    handle_event(Event(type="quote.updated", data={"user_id": user_id}), db=db_session)

    assert _nobody_rows(db_session) == []


def test_an_event_with_no_user_records_nothing(db_session):
    handle_event(Event(type="order.created", data={}), db=db_session)

    assert _nobody_rows(db_session) == []


# --- it must not become work for the retry sweep ----------------------------


def test_the_record_is_never_retried(db_session):
    """There is nothing to retry it TO. A due date here would make the sweep
    pick it up forever and fail on a NULL channel."""
    user_id = _user_id(db_session)

    handle_event(_event(user_id), db=db_session)

    assert _nobody_rows(db_session)[0].next_retry_at is None


def test_the_retry_sweep_steps_over_it(db_session):
    """Defence in depth: even if a due date got set, the sweep must not crash
    trying to load channel None. A sweep that raises stops retrying every OTHER
    pending notification too."""
    from app.services.notification import retry

    user_id = _user_id(db_session)
    handle_event(_event(user_id), db=db_session)
    row = _nobody_rows(db_session)[0]
    row.next_retry_at = row.created_at
    db_session.commit()

    retry.retry_pending(db_session)  # must not raise

    db_session.refresh(row)
    assert row.next_retry_at is None, "and it must stop being due"
