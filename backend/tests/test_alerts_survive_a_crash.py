"""Three ways the app could be silent while looking perfectly healthy.

## A tick that crashes takes the whole batch of alerts with it

tick_once collects events in a local list and publishes them at the very end,
after the try/finally. Any exception in the body skips the publish entirely and
the batch evaporates, leaving one line on stderr.

What makes that permanent rather than merely late: _record_strategy_error
switches a strategy off and COMMITS that, then only appends the
「策略已停用」 event to the list. The next tick queries `is_active.is_(True)`, so
the strategy is gone from it -- the error threshold is crossed exactly once in
a strategy's life, and there is no path that re-raises the event. The retry
sweep cannot help either: it resends existing FAILED NotificationLog rows, and
this alert never reached the dispatcher, so no row exists.

So the owner's strategy is permanently switched off, every future alert from it
is gone with it, and nothing anywhere told them.

## The test button reports success while the whole system is muted

NOTIFICATIONS_ENABLED=false makes handle_event return before doing anything,
and main.py does not even subscribe the dispatcher. But test_channel calls the
sender directly and never looks at the flag -- so 測試 goes green, a real push
arrives on the phone, and a 已送出 row appears in the ledger, while every
genuine alert is being discarded. It manufactures evidence for the opposite of
the truth, on the one screen built to answer this question.

## A given-up alert still claims it is coming

When the sweep abandons a deferred alert, the row keeps
「靜音時段，將在 07:00 UTC 之後送出」 in its error field. The history page then
shows a notification that is queued and will never be sent.
"""

from datetime import timedelta

import pytest

from app.models.enums import ChannelType, NotificationStatus
from app.models.mixins import utcnow
from app.models.notification import NotificationChannel, NotificationLog
from app.services import market_loop
from app.services.events import Event
from app.services.notification import retry


def _user_id(db_session) -> int:
    from app.models.user import User

    user = db_session.query(User).first()
    if user is None:
        user = User(email="crash@example.com", hashed_password="x")
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    return user.id


# --- a crashing tick must not swallow what it already decided ---------------


def test_events_queued_before_a_crash_are_still_published(db_session, monkeypatch):
    """The batch is the point. A strategy switched off during this tick has
    already been committed as inactive -- if its notice is lost here it is lost
    for good, because the next tick only looks at active strategies."""
    published: list[Event] = []
    monkeypatch.setattr(market_loop.bus, "publish", lambda event: published.append(event))

    def _append_then_explode(db, events):
        events.append(Event(type="strategy.error", data={"user_id": 1, "strategy_id": 7}))
        raise RuntimeError("provider blew up mid-tick")

    monkeypatch.setattr(market_loop, "_expire_stale_orders", _append_then_explode)

    with pytest.raises(RuntimeError):
        market_loop.tick_once(db=db_session)

    assert [event.type for event in published] == ["strategy.error"], (
        "the tick crashed and took the alert with it"
    )


def test_the_publish_is_not_skipped_by_an_exception(db_session, monkeypatch):
    """Pinned structurally, because reproducing a mid-tick crash with a real
    strategy is fragile: the publish must run from a finally, not from a line
    after the try."""
    import inspect

    source = inspect.getsource(market_loop.tick_once)
    body_after_finally = source.split("finally:")[-1]

    assert "bus.publish" in body_after_finally, (
        "events are published outside the try/finally, so any exception in the "
        "tick loses the whole batch -- including 策略已停用, which never fires again"
    )


# --- the test button must not lie about a muted system ----------------------


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


def test_the_test_button_refuses_to_pass_while_notifications_are_off(
    auth_client, db_session, monkeypatch
):
    """It used to send a real message and report success while every genuine
    alert was being dropped -- evidence for the opposite of the truth."""
    monkeypatch.setattr("app.config.settings.NOTIFICATIONS_ENABLED", False)
    channel = _channel(db_session, _user_id(db_session))

    body = auth_client.post(f"/api/notifications/channels/{channel.id}/test").json()

    assert body["ok"] is False
    assert "NOTIFICATIONS_ENABLED" in (body["error"] or ""), body


def test_it_says_the_setting_is_the_problem_not_the_channel(auth_client, db_session, monkeypatch):
    monkeypatch.setattr("app.config.settings.NOTIFICATIONS_ENABLED", False)
    channel = _channel(db_session, _user_id(db_session))

    error = auth_client.post(f"/api/notifications/channels/{channel.id}/test").json()["error"]

    assert "伺服器" in error, error


def test_nothing_is_actually_sent_while_notifications_are_off(auth_client, db_session, monkeypatch):
    monkeypatch.setattr("app.config.settings.NOTIFICATIONS_ENABLED", False)
    channel = _channel(db_session, _user_id(db_session))

    sent: list[str] = []
    monkeypatch.setattr(
        "app.services.notification.telegram.TelegramSender.send",
        lambda self, config, message: sent.append(message),
    )
    auth_client.post(f"/api/notifications/channels/{channel.id}/test")

    assert sent == [], "a muted system must not prove itself with a real message"


def test_the_health_check_reports_that_notifications_are_off(client, monkeypatch):
    """The external watchdog is the only thing that looks when nobody is
    looking, and for this product a muted notifier is not a healthy one."""
    monkeypatch.setattr("app.config.settings.NOTIFICATIONS_ENABLED", False)

    body = client.get("/healthz").json()

    assert body["checks"]["notifications"]["status"] != "ok"


def test_the_health_check_is_quiet_when_notifications_are_on(client, monkeypatch):
    # conftest switches notifications off for the whole suite, so this has to
    # put them back explicitly rather than relying on the default.
    monkeypatch.setattr("app.config.settings.NOTIFICATIONS_ENABLED", True)

    body = client.get("/healthz").json()

    assert body["checks"]["notifications"]["status"] == "ok"


# --- a given-up alert must stop claiming it is coming -----------------------


def test_an_abandoned_deferral_stops_saying_it_will_be_sent(db_session):
    """The row kept 「將在 07:00 UTC 之後送出」 after the sweep had given up on
    it, so the history page showed a queued notification that would never
    arrive."""
    user_id = _user_id(db_session)
    channel = _channel(db_session, user_id)
    log = NotificationLog(
        user_id=user_id,
        channel_id=channel.id,
        event="order.created",
        status=NotificationStatus.FAILED,
        error="靜音時段，將在 07:00 UTC 之後送出",
        message="2330.TW 跌破 950",
        attempts=0,
        next_retry_at=utcnow() - timedelta(hours=8),
    )
    db_session.add(log)
    db_session.commit()
    log.created_at = utcnow() - timedelta(hours=20)
    db_session.commit()

    retry.retry_pending(db_session)

    db_session.refresh(log)
    assert log.next_retry_at is None, "it was given up on"
    # The invariant is that the message LEADS with the abandonment, not that a
    # particular substring is absent: the original reason is kept after
    # 「原因：」 on purpose, because it is the only diagnostic left, and framing
    # it as a past state is what stops it reading as a promise.
    assert (log.error or "").startswith("超過"), log.error
    assert "已放棄" in (log.error or ""), log.error
    assert "不會再重送" in (log.error or ""), log.error
