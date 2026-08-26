"""One broken channel must not silence the others.

Only `sender.send` was inside a try. Everything else in the per-channel loop
ran bare: the quiet-hours calculation, both `session.commit()` calls, and
`retry.schedule_first_retry`. Any of those raising escapes the whole loop, so
the channels after it in the list are never even attempted -- and the exception
surfaces one line up in `bus.publish`, which the market loop logs and moves on
from.

The result is an alert that reached some of the owner's channels and not
others, for a reason unrelated to any of them, with no row explaining why the
rest were skipped. Having several channels is supposed to be what makes one of
them failing survivable; this made one of them failing take the rest down.

A malformed quiet-hours pair is the realistic trigger: hours come from the API
as plain integers and a bad timezone string on the user row reaches
`zoneinfo.ZoneInfo` inside the calculation.
"""

from app.enums import ChannelType, NotificationStatus
from app.models.notification import NotificationChannel, NotificationLog
from app.services.events import Event
from app.services.notification.base import SendResult
from app.services.notification.dispatcher import handle_event


def _user_id(db_session) -> int:
    from app.models.user import User

    user = db_session.query(User).first()
    if user is None:
        user = User(email="iso@example.com", hashed_password="x")
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    return user.id


def _channel(db_session, user_id: int, label: str, **kw) -> NotificationChannel:
    defaults = dict(
        channel_type=ChannelType.TELEGRAM,
        config_encrypted={"bot_token": "t", "chat_id": "1"},
        is_enabled=True,
    )
    defaults.update(kw)
    channel = NotificationChannel(user_id=user_id, label=label, **defaults)
    db_session.add(channel)
    db_session.commit()
    db_session.refresh(channel)
    return channel


def _event(user_id: int) -> Event:
    return Event(type="order.created", data={"user_id": user_id, "order_id": 1})


def test_a_channel_that_blows_up_does_not_stop_the_next_one(db_session, monkeypatch):
    """The whole point of having more than one channel."""
    user_id = _user_id(db_session)
    first = _channel(db_session, user_id, "explodes")
    second = _channel(db_session, user_id, "works")

    def _quiet(start, end, tz, at=None):
        # Raises for the first channel only, the way a malformed pair or a bad
        # timezone would.
        raise ValueError("bad quiet hours")

    # Patched on the real module, not on `dispatcher`: dispatcher imports
    # quiet_hours inside the function to avoid a circular import, so there is
    # no attribute on the dispatcher module to replace.
    monkeypatch.setattr("app.services.notification.quiet_hours.is_quiet", _quiet)
    monkeypatch.setattr(
        "app.services.notification.telegram.TelegramSender.send",
        lambda *a, **k: SendResult(ok=True),
    )

    handle_event(_event(user_id), db=db_session)

    # Neither channel could compute quiet hours, so neither sends -- but the
    # loop must have REACHED both and recorded both, rather than escaping on
    # the first.
    rows = db_session.query(NotificationLog).all()
    assert {row.channel_id for row in rows} == {first.id, second.id}


def test_the_failure_is_recorded_against_the_channel_it_happened_on(db_session, monkeypatch):
    """Swallowing it silently would trade one visible failure for an invisible
    one, which for this product is the worse trade."""
    user_id = _user_id(db_session)
    channel = _channel(db_session, user_id, "explodes")

    monkeypatch.setattr(
        "app.services.notification.quiet_hours.is_quiet",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("bad quiet hours")),
    )

    handle_event(_event(user_id), db=db_session)

    row = db_session.query(NotificationLog).filter_by(channel_id=channel.id).one()
    assert row.status == NotificationStatus.FAILED
    assert "bad quiet hours" in (row.error or "")


def test_the_alert_is_still_queued_for_retry(db_session, monkeypatch):
    """A crash on one channel is a transient failure like any other, and this
    event fires once -- dropping it because of a code fault would lose it for
    good."""
    user_id = _user_id(db_session)
    channel = _channel(db_session, user_id, "explodes")

    monkeypatch.setattr(
        "app.services.notification.quiet_hours.is_quiet",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")),
    )

    handle_event(_event(user_id), db=db_session)

    row = db_session.query(NotificationLog).filter_by(channel_id=channel.id).one()
    assert row.next_retry_at is not None
    assert row.message, "there is nothing to resend without it"


def test_a_working_channel_is_unaffected(db_session, monkeypatch):
    user_id = _user_id(db_session)
    _channel(db_session, user_id, "works")

    monkeypatch.setattr(
        "app.services.notification.telegram.TelegramSender.send",
        lambda *a, **k: SendResult(ok=True),
    )

    result = handle_event(_event(user_id), db=db_session)

    assert result.delivered == 1


def test_one_crashing_channel_does_not_make_the_alert_look_unreachable(db_session, monkeypatch):
    """If the crash were swallowed without a row, the "reached nobody" recorder
    would fire as well and the ledger would carry two contradictory stories
    about one alert."""
    user_id = _user_id(db_session)
    _channel(db_session, user_id, "explodes")

    monkeypatch.setattr(
        "app.services.notification.quiet_hours.is_quiet",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")),
    )

    handle_event(_event(user_id), db=db_session)

    orphans = db_session.query(NotificationLog).filter(NotificationLog.channel_id.is_(None)).all()
    assert orphans == []
