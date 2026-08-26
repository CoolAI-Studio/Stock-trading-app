"""Telling the owner that one of their channels just stopped working.

When a send fails permanently -- a push subscription iOS discarded, a revoked
Telegram token -- the retry sweep switches the channel off, because retrying
cannot help and a channel that says 啟用中 while every alert posts into a void
is worse than one that says it is off.

But the only record was `channel.last_error`, a string on a row that is visible
on exactly one page. The owner had to already suspect something and go looking.
Meanwhile every alert that channel would have carried was simply not arriving.

So the notice goes out through the channels that still work. That is the whole
point of having more than one.

BOUNDED, because a notice is itself a notification and this is the shape of an
infinite loop: notice -> sent through channel B -> B fails permanently -> B is
disabled -> notice about B -> ... The bounds are that the notice is sent once,
directly, with no retry scheduled and no permanent-failure handling of its own.
A notice that fails is logged and dropped.

AND WHEN THERE IS NOTHING LEFT. If the disabled channel was the last one, no
notice can reach anybody -- that is what "last channel" means, and inventing a
mechanism would be a lie. It is recorded in the ledger instead, on a row with
no channel, so it is discoverable the moment the owner opens the app.
"""

from datetime import timedelta

from app.enums import ChannelType, NotificationStatus
from app.models.mixins import utcnow
from app.models.notification import NotificationChannel, NotificationLog
from app.services.notification import retry


def _user_id(db_session) -> int:
    from app.models.user import User

    user = db_session.query(User).first()
    if user is None:
        user = User(email="notice@example.com", hashed_password="x")
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


def _due_log(db_session, user_id: int, channel: NotificationChannel) -> NotificationLog:
    """A notification already queued for retry on `channel`, due now."""
    log = NotificationLog(
        user_id=user_id,
        channel_id=channel.id,
        event="order.created",
        status=NotificationStatus.FAILED,
        error="HTTP 500",
        message="2330.TW 跌破 950",
        attempts=1,
        next_retry_at=utcnow() - timedelta(seconds=1),
    )
    db_session.add(log)
    db_session.commit()
    db_session.refresh(log)
    return log


def _fail_permanently(monkeypatch, code: str = "HTTP 410") -> None:
    from app.services.notification.base import SendResult

    monkeypatch.setattr(
        "app.services.notification.telegram.TelegramSender.send",
        lambda *a, **k: SendResult(ok=False, error=code),
    )


# --- the notice goes out ----------------------------------------------------


def test_disabling_a_channel_tells_the_owner_through_the_others(db_session, monkeypatch):
    user_id = _user_id(db_session)
    dying = _channel(db_session, user_id, "iphone")
    _channel(db_session, user_id, "email-backup")
    _due_log(db_session, user_id, dying)

    sent: list[tuple[dict, str]] = []
    _fail_permanently(monkeypatch)
    monkeypatch.setattr(
        retry,
        "_send_notice",
        lambda db, channel, text: sent.append((channel.label, text)),
    )

    retry.retry_pending(db_session)

    db_session.refresh(dying)
    assert dying.is_enabled is False
    assert [label for label, _ in sent] == ["email-backup"]


def test_the_notice_names_the_channel_that_died(db_session, monkeypatch):
    """ "A channel was disabled" sends the owner to check all of them."""
    user_id = _user_id(db_session)
    dying = _channel(db_session, user_id, "iphone")
    _channel(db_session, user_id, "email-backup")
    _due_log(db_session, user_id, dying)

    sent: list[str] = []
    _fail_permanently(monkeypatch)
    monkeypatch.setattr(retry, "_send_notice", lambda db, channel, text: sent.append(text))

    retry.retry_pending(db_session)

    assert "iphone" in sent[0], sent


def test_the_notice_says_what_to_do_about_it(db_session, monkeypatch):
    user_id = _user_id(db_session)
    dying = _channel(db_session, user_id, "iphone")
    _channel(db_session, user_id, "email-backup")
    _due_log(db_session, user_id, dying)

    sent: list[str] = []
    _fail_permanently(monkeypatch)
    monkeypatch.setattr(retry, "_send_notice", lambda db, channel, text: sent.append(text))

    retry.retry_pending(db_session)

    # Pinned against _permanent_explanation itself rather than a substring: the
    # actionable sentence differs per channel type (web push talks about
    # re-subscribing, Telegram about the bot token), and what matters is that
    # the notice carries THAT sentence instead of inventing a vaguer one.
    expected = retry._permanent_explanation(ChannelType.TELEGRAM, "HTTP 410")
    assert expected in sent[0], sent


def test_the_dead_channel_is_not_sent_its_own_obituary(db_session, monkeypatch):
    user_id = _user_id(db_session)
    dying = _channel(db_session, user_id, "iphone")
    _due_log(db_session, user_id, dying)

    sent: list[str] = []
    _fail_permanently(monkeypatch)
    monkeypatch.setattr(retry, "_send_notice", lambda db, channel, text: sent.append(channel.label))

    retry.retry_pending(db_session)

    assert "iphone" not in sent


def test_a_disabled_channel_is_not_used_to_carry_the_notice(db_session, monkeypatch):
    user_id = _user_id(db_session)
    dying = _channel(db_session, user_id, "iphone")
    _channel(db_session, user_id, "switched-off", is_enabled=False)
    _due_log(db_session, user_id, dying)

    sent: list[str] = []
    _fail_permanently(monkeypatch)
    monkeypatch.setattr(retry, "_send_notice", lambda db, channel, text: sent.append(channel.label))

    retry.retry_pending(db_session)

    assert sent == []


# --- nothing left to tell them with -----------------------------------------


def test_losing_the_last_channel_is_recorded_in_the_ledger(db_session, monkeypatch):
    """No notice can reach anybody -- that is what "last channel" means. The
    honest thing is to record it where the owner will see it the moment they
    open the app, not to invent a delivery mechanism that does not exist."""
    user_id = _user_id(db_session)
    dying = _channel(db_session, user_id, "only-one")
    _due_log(db_session, user_id, dying)

    _fail_permanently(monkeypatch)
    retry.retry_pending(db_session)

    orphan = db_session.query(NotificationLog).filter(NotificationLog.channel_id.is_(None)).one()
    assert orphan.status == NotificationStatus.FAILED
    assert "only-one" in (orphan.error or "")


def test_that_record_says_there_is_now_no_way_to_reach_them(db_session, monkeypatch):
    user_id = _user_id(db_session)
    dying = _channel(db_session, user_id, "only-one")
    _due_log(db_session, user_id, dying)

    _fail_permanently(monkeypatch)
    retry.retry_pending(db_session)

    orphan = db_session.query(NotificationLog).filter(NotificationLog.channel_id.is_(None)).one()
    assert "收不到" in (orphan.error or "") or "沒有" in (orphan.error or ""), orphan.error


# --- it must not become a loop or a hazard ----------------------------------


def test_the_notice_is_never_queued_for_retry(db_session, monkeypatch):
    """A notice is itself a notification. Retrying it means notice -> fails ->
    disable -> notice -> ... Sent once, directly, and dropped if it fails."""
    user_id = _user_id(db_session)
    dying = _channel(db_session, user_id, "iphone")
    survivor = _channel(db_session, user_id, "email-backup")
    _due_log(db_session, user_id, dying)

    _fail_permanently(monkeypatch)
    retry.retry_pending(db_session)

    notices = (
        db_session.query(NotificationLog).filter(NotificationLog.channel_id == survivor.id).all()
    )
    assert notices, "the notice attempt is still recorded"
    assert all(n.next_retry_at is None for n in notices)


def test_a_notice_that_fails_does_not_disable_the_channel_that_carried_it(db_session, monkeypatch):
    """Otherwise one dead channel takes every other channel down with it, one
    sweep at a time."""
    user_id = _user_id(db_session)
    dying = _channel(db_session, user_id, "iphone")
    survivor = _channel(db_session, user_id, "email-backup")
    _due_log(db_session, user_id, dying)

    # Everything fails permanently, including the notice.
    _fail_permanently(monkeypatch)
    retry.retry_pending(db_session)

    db_session.refresh(survivor)
    assert survivor.is_enabled is True


def test_the_sweep_survives_a_notice_that_raises(db_session, monkeypatch):
    """A sweep that dies stops retrying every other pending notification too."""
    user_id = _user_id(db_session)
    dying = _channel(db_session, user_id, "iphone")
    _channel(db_session, user_id, "email-backup")
    _due_log(db_session, user_id, dying)

    _fail_permanently(monkeypatch)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("notice blew up")

    monkeypatch.setattr(retry, "_send_notice", _boom)

    retry.retry_pending(db_session)  # must not raise

    db_session.refresh(dying)
    assert dying.is_enabled is False, "the disable still happened"


def test_an_ordinary_retry_failure_sends_no_notice(db_session, monkeypatch):
    """Only a PERMANENT failure disables a channel. A 500 is retried, and
    announcing every transient blip would be exactly the noise that makes
    somebody stop reading these."""
    user_id = _user_id(db_session)
    channel = _channel(db_session, user_id, "iphone")
    _channel(db_session, user_id, "email-backup")
    _due_log(db_session, user_id, channel)

    from app.services.notification.base import SendResult

    monkeypatch.setattr(
        "app.services.notification.telegram.TelegramSender.send",
        lambda *a, **k: SendResult(ok=False, error="HTTP 503"),
    )
    sent: list[str] = []
    monkeypatch.setattr(retry, "_send_notice", lambda db, ch, text: sent.append(text))

    retry.retry_pending(db_session)

    assert sent == []
    db_session.refresh(channel)
    assert channel.is_enabled is True
