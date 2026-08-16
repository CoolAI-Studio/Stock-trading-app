from unittest.mock import MagicMock, patch

from app.models.enums import ChannelType, NotificationStatus
from app.models.notification import NotificationChannel, NotificationLog
from app.models.user import User
from app.services.events import Event
from app.services.notification.dispatcher import handle_event
from app.services.notification.email import EmailSender
from app.services.notification.line import LineSender
from app.services.notification.telegram import TelegramSender


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
    import httpx

    with patch("httpx.post", side_effect=httpx.ConnectError("boom")):
        result = TelegramSender().send({"bot_token": "t", "chat_id": "123"}, "hello")

    assert result.ok is False


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
    assert db_session.query(NotificationLog).count() == 0


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


def test_dispatcher_ignores_events_without_user_id(db_session):
    with patch("httpx.post") as mock_post:
        handle_event(Event(type="quote.update", data={"symbols": ["AAPL"]}), db=db_session)

    mock_post.assert_not_called()
