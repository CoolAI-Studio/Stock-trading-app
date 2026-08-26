from unittest.mock import MagicMock, patch

import httpx

from app.enums import ChannelType, NotificationStatus
from app.models.notification import NotificationChannel, NotificationLog
from app.models.user import User
from app.services.events import Event
from app.services.notification.dispatcher import handle_event
from app.services.notification.email import EmailSender
from app.services.notification.line import LineSender
from app.services.notification.telegram import TelegramSender
from app.services.notification.webpush import WebPushSender


def _make_user(db_session, email="notify@example.com") -> User:
    user = User(email=email, hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


# ---- senders (mocked network) ----


def test_telegram_sender_success():
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"ok": True}
    fake_response.raise_for_status.return_value = None
    with patch("httpx.post", return_value=fake_response) as mock_post:
        result = TelegramSender().send({"bot_token": "t", "chat_id": "123"}, "hello")

    assert result.ok is True
    mock_post.assert_called_once()


def test_telegram_sender_missing_config():
    result = TelegramSender().send({}, "hello")
    assert result.ok is False
    assert "bot_token" in result.error


def test_telegram_sender_http_failure():
    with patch("httpx.post", side_effect=httpx.ConnectError("boom")):
        result = TelegramSender().send({"bot_token": "t", "chat_id": "123"}, "hello")

    assert result.ok is False


# A send failure is written verbatim into NotificationLog.error and
# NotificationChannel.last_error -- plain Text columns, sitting right next to
# the deliberately Fernet-encrypted config -- and both are served back by the
# API. So whatever a sender puts in SendResult.error is effectively public,
# and must never contain a credential.
_BOT_TOKEN = "7654321:AAHthis-bot-token-must-never-be-echoed"


def _telegram_401(token: str) -> httpx.Response:
    """A real httpx.Response rather than a MagicMock: the leak under test is
    created by httpx itself, which formats the request URL -- and the token
    embedded in Telegram's URL -- into the HTTPStatusError message. A mock
    that fabricates the exception would not reproduce it."""
    request = httpx.Request("POST", f"https://api.telegram.org/bot{token}/sendMessage")
    return httpx.Response(
        401,
        json={"ok": False, "error_code": 401, "description": "Unauthorized"},
        request=request,
    )


def test_telegram_status_error_reports_the_code_without_the_bot_token():
    with patch("httpx.post", return_value=_telegram_401(_BOT_TOKEN)):
        result = TelegramSender().send({"bot_token": _BOT_TOKEN, "chat_id": "123"}, "hello")

    assert result.ok is False
    assert _BOT_TOKEN not in result.error
    # Still diagnostic: the owner needs to know it was rejected, and why.
    assert "401" in result.error
    assert "Unauthorized" in result.error


def test_telegram_transport_error_never_contains_the_bot_token():
    """Not every httpx failure is an HTTP status. A transport error can carry
    the URL in its own message too, so the guard cannot be status-only."""
    request = httpx.Request("POST", f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage")
    exc = httpx.ConnectError(f"failed to connect to {request.url}", request=request)

    with patch("httpx.post", side_effect=exc):
        result = TelegramSender().send({"bot_token": _BOT_TOKEN, "chat_id": "123"}, "hello")

    assert result.ok is False
    assert _BOT_TOKEN not in result.error


def test_line_error_never_contains_the_access_token():
    """LINE authenticates with an Authorization header, not a token in the
    URL, so httpx's message should already be clean. Verified rather than
    assumed -- this is the sibling sender the Telegram leak prompted a check
    of, and a regression here would be just as invisible."""
    access_token = "line-channel-access-token-DO-NOT-LEAK"
    request = httpx.Request("POST", "https://api.line.me/v2/bot/message/push")
    response = httpx.Response(401, json={"message": "Authentication failed"}, request=request)

    with patch("httpx.post", return_value=response):
        result = LineSender().send({"access_token": access_token, "to": "u123"}, "hello")

    assert result.ok is False
    assert access_token not in result.error


def test_line_sender_success():
    fake_response = MagicMock(status_code=200)
    fake_response.raise_for_status.return_value = None
    with patch("httpx.post", return_value=fake_response) as mock_post:
        result = LineSender().send({"access_token": "a", "to": "u123"}, "hello")

    assert result.ok is True
    mock_post.assert_called_once()


def test_email_sender_success():
    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_smtp = MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = mock_smtp
        result = EmailSender().send(
            {
                "host": "smtp.example.com",
                "from_addr": "bot@example.com",
                "to_addr": "me@example.com",
                "username": "bot@example.com",
                "password": "secret",
            },
            "hello",
        )

    assert result.ok is True
    mock_smtp.send_message.assert_called_once()


def test_email_sender_missing_config():
    result = EmailSender().send({}, "hello")
    assert result.ok is False


def test_webpush_sender_success(monkeypatch):
    monkeypatch.setattr("app.config.settings.VAPID_PRIVATE_KEY", "test-private-key")
    with patch("app.services.notification.webpush.webpush", return_value=None) as mock_send:
        result = WebPushSender().send(
            {"endpoint": "https://push.example.com/x", "p256dh": "p", "auth": "a"}, "hello"
        )

    assert result.ok is True
    mock_send.assert_called_once()
    # Regression guard: pywebpush's own default (ttl=0) is rejected outright
    # by Microsoft's WNS push service (verified live against a real Edge
    # subscription -- 400 "Ttl value conflicts with X-WNS-Cache-Policy").
    assert mock_send.call_args.kwargs["ttl"] > 0


def test_webpush_sender_missing_config():
    result = WebPushSender().send({}, "hello")
    assert result.ok is False
    assert "endpoint" in result.error


def test_webpush_sender_missing_vapid_key(monkeypatch):
    monkeypatch.setattr("app.config.settings.VAPID_PRIVATE_KEY", "")
    result = WebPushSender().send(
        {"endpoint": "https://push.example.com/x", "p256dh": "p", "auth": "a"}, "hello"
    )
    assert result.ok is False
    assert "VAPID_PRIVATE_KEY" in result.error


def test_webpush_sender_gone_subscription(monkeypatch):
    from pywebpush import WebPushException

    monkeypatch.setattr("app.config.settings.VAPID_PRIVATE_KEY", "test-private-key")
    with patch(
        "app.services.notification.webpush.webpush",
        side_effect=WebPushException("Push failed: 410 Gone"),
    ):
        result = WebPushSender().send(
            {"endpoint": "https://push.example.com/x", "p256dh": "p", "auth": "a"}, "hello"
        )

    assert result.ok is False
    assert "410" in result.error


# ---- dispatcher ----


def test_dispatcher_sends_to_enabled_channel_and_logs_success(db_session):
    user = _make_user(db_session)
    channel = NotificationChannel(
        user_id=user.id,
        channel_type=ChannelType.TELEGRAM,
        label="my-telegram",
        config_encrypted={"bot_token": "t", "chat_id": "123"},
        is_enabled=True,
    )
    db_session.add(channel)
    db_session.commit()

    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"ok": True}
    fake_response.raise_for_status.return_value = None
    with patch("httpx.post", return_value=fake_response):
        handle_event(
            Event(type="order.created", data={"order_id": 1, "user_id": user.id}), db=db_session
        )

    log = db_session.query(NotificationLog).first()
    assert log is not None
    assert log.status == NotificationStatus.SENT
    assert log.channel_id == channel.id

    db_session.refresh(channel)
    assert channel.last_sent_at is not None


def test_dispatcher_logs_failure_without_crashing(db_session):
    user = _make_user(db_session)
    db_session.add(
        NotificationChannel(
            user_id=user.id,
            channel_type=ChannelType.TELEGRAM,
            label="my-telegram",
            config_encrypted={"bot_token": "t", "chat_id": "123"},
            is_enabled=True,
        )
    )
    db_session.commit()

    import httpx

    with patch("httpx.post", side_effect=httpx.ConnectError("network down")):
        handle_event(
            Event(type="order.created", data={"order_id": 1, "user_id": user.id}), db=db_session
        )

    log = db_session.query(NotificationLog).first()
    assert log.status == NotificationStatus.FAILED
    assert log.error


def test_dispatcher_skips_disabled_channels(db_session):
    user = _make_user(db_session)
    db_session.add(
        NotificationChannel(
            user_id=user.id,
            channel_type=ChannelType.TELEGRAM,
            label="disabled",
            config_encrypted={"bot_token": "t", "chat_id": "123"},
            is_enabled=False,
        )
    )
    db_session.commit()

    with patch("httpx.post") as mock_post:
        handle_event(
            Event(type="order.created", data={"order_id": 1, "user_id": user.id}), db=db_session
        )

    mock_post.assert_not_called()

    # This assertion used to read `count() == 0`, which pinned the bug rather
    # than the behaviour: an alert raised for somebody whose only channel is
    # switched off left NO trace, so the ledger looked exactly like an
    # afternoon on which nothing happened. Nothing is SENT -- that part was
    # always right -- but the miss is now recorded, on a row with no channel.
    # See tests/test_alerts_reaching_nobody.py.
    rows = db_session.query(NotificationLog).all()
    assert len(rows) == 1
    assert rows[0].channel_id is None
    assert rows[0].status == NotificationStatus.FAILED


def test_dispatcher_respects_subscribed_events_filter(db_session):
    user = _make_user(db_session)
    db_session.add(
        NotificationChannel(
            user_id=user.id,
            channel_type=ChannelType.TELEGRAM,
            label="orders-only",
            config_encrypted={"bot_token": "t", "chat_id": "123"},
            is_enabled=True,
            subscribed_events=["order.updated"],
        )
    )
    db_session.commit()

    with patch("httpx.post") as mock_post:
        handle_event(
            Event(type="order.created", data={"order_id": 1, "user_id": user.id}), db=db_session
        )

    mock_post.assert_not_called()
    # The filter is doing its job, but the alert still reached nobody, and that
    # needs saying -- with a different message from "you have no channels",
    # because the fix is different: tick this event on the channel you have.
    row = db_session.query(NotificationLog).one()
    assert row.channel_id is None
    assert "事件" in (row.error or "")


def test_dispatcher_sends_to_web_push_channel(db_session, monkeypatch):
    monkeypatch.setattr("app.config.settings.VAPID_PRIVATE_KEY", "test-private-key")
    user = _make_user(db_session)
    channel = NotificationChannel(
        user_id=user.id,
        channel_type=ChannelType.WEB_PUSH,
        label="my-laptop",
        config_encrypted={"endpoint": "https://push.example.com/x", "p256dh": "p", "auth": "a"},
        is_enabled=True,
    )
    db_session.add(channel)
    db_session.commit()

    with patch("app.services.notification.webpush.webpush", return_value=None):
        handle_event(
            Event(type="order.created", data={"order_id": 1, "user_id": user.id}), db=db_session
        )

    log = db_session.query(NotificationLog).first()
    assert log is not None
    assert log.status == NotificationStatus.SENT
    assert log.channel_id == channel.id


def test_dispatcher_ignores_events_without_user_id(db_session):
    with patch("httpx.post") as mock_post:
        handle_event(Event(type="quote.update", data={"symbols": ["AAPL"]}), db=db_session)

    mock_post.assert_not_called()
