"""Proving a push actually reached the phone, instead of assuming it.

THE BUG. POST /channels/{id}/test reported 已送出 whenever pywebpush got a 2xx.
RFC 8030 §5 says in as many words what that 2xx means: "A 201 (Created)
response indicates that the push message was accepted... This does not indicate
that the message was delivered to the user agent." Apple returns 201 the
instant it accepts a message for later delivery -- so the test passed with the
phone switched off, with notifications disabled for the web app, and with a
subscription iOS had already thrown away.

That is the worst defect available in this product: the one button whose entire
job is to answer 「我的提醒到底會不會送到？」 answered yes when the answer was no.

THE FIX. A single-use token travels inside the push payload; the service
worker posts it back after it has displayed the notification. Only then is the
send recorded as delivered.

WHY THE RECEIPT ENDPOINT CAN BE UNAUTHENTICATED. A service worker has no
access to the app's JWT. It does not need one: RFC 8291 encrypts the payload
end to end with keys only that subscription holds -- the push service itself
cannot read it -- so holding the token IS proof that the intended device
decrypted the message. Nobody else can forge a receipt, Apple included. The
token is cleared on use, so a replay does nothing, and the endpoint answers
204 whether or not the token existed, so it cannot be used to test tokens.

WHAT IT STILL DOES NOT PROVE, and the wording must not claim otherwise: that a
human saw it. It proves the notification was displayed on the device.
"""

import json
from unittest.mock import patch

import pytest

from app.enums import ChannelType
from app.models.notification import NotificationChannel, NotificationLog


@pytest.fixture(autouse=True)
def _notifications_on(client, monkeypatch):
    """conftest switches NOTIFICATIONS_ENABLED off for the whole suite so that
    nothing sends for real. Every test in this file is ABOUT the 測試 button,
    which now refuses to run while the notifier is muted (it used to report
    success and manufacture evidence for the opposite of the truth), so the
    flag has to be put back explicitly here.

    Depends on `client` rather than `auth_client` for two reasons: conftest
    does the muting inside `client`, so this has to be ordered after it -- and
    `auth_client` attaches a bearer token to that same client, which would
    quietly authenticate the one test here that must NOT be logged in."""
    monkeypatch.setattr("app.config.settings.NOTIFICATIONS_ENABLED", True)


SUBSCRIPTION = {
    "endpoint": "https://web.push.apple.com/abc",
    "p256dh": "p256dh-value",
    "auth": "auth-value",
}


def _channel(db_session, user_id: int) -> NotificationChannel:
    channel = NotificationChannel(
        user_id=user_id,
        channel_type=ChannelType.WEB_PUSH,
        label="iphone",
        config_encrypted=dict(SUBSCRIPTION),
        is_enabled=True,
    )
    db_session.add(channel)
    db_session.commit()
    db_session.refresh(channel)
    return channel


def _user_id(db_session) -> int:
    from app.models.user import User

    return db_session.query(User).first().id


def _run_test(auth_client, channel_id: int):
    """Presses 測試 with the actual network call stubbed, and hands back both
    the response body and the payload pywebpush was given."""
    with patch("app.services.notification.webpush.webpush", return_value=None) as mock:
        resp = auth_client.post(f"/api/notifications/channels/{channel_id}/test")
    payload = json.loads(mock.call_args.kwargs["data"]) if mock.call_args else {}
    return resp, payload


# --- the token gets out ------------------------------------------------------


def test_a_test_push_carries_a_receipt_token(auth_client, db_session, monkeypatch):
    monkeypatch.setattr("app.config.settings.VAPID_PRIVATE_KEY", "private-key")
    channel = _channel(db_session, _user_id(db_session))

    _resp, payload = _run_test(auth_client, channel.id)

    assert payload.get("receipt"), "nothing for the device to report back with"


def test_the_token_is_recorded_against_the_log_row(auth_client, db_session, monkeypatch):
    monkeypatch.setattr("app.config.settings.VAPID_PRIVATE_KEY", "private-key")
    channel = _channel(db_session, _user_id(db_session))

    _resp, payload = _run_test(auth_client, channel.id)

    log = db_session.query(NotificationLog).filter_by(receipt_token=payload["receipt"]).one()
    assert log.channel_id == channel.id
    assert log.delivered_at is None, "nothing has confirmed anything yet"


def test_the_response_says_which_log_to_watch(auth_client, db_session, monkeypatch):
    """The UI has to poll something specific. Without an id it would have to
    guess from the log list, which races with any other notification."""
    monkeypatch.setattr("app.config.settings.VAPID_PRIVATE_KEY", "private-key")
    channel = _channel(db_session, _user_id(db_session))

    resp, _payload = _run_test(auth_client, channel.id)

    assert isinstance(resp.json()["log_id"], int)


def test_a_failed_send_still_returns_a_log_id_so_the_ui_can_stop_waiting(
    auth_client, db_session, monkeypatch
):
    monkeypatch.setattr("app.config.settings.VAPID_PRIVATE_KEY", "")
    channel = _channel(db_session, _user_id(db_session))

    resp = auth_client.post(f"/api/notifications/channels/{channel.id}/test")

    assert resp.json()["ok"] is False
    assert resp.json()["log_id"] is not None


def test_a_non_push_channel_gets_no_token(auth_client, db_session):
    """Telegram and email have no service worker to report back, so a token
    would be minted and never redeemed -- and the UI would wait for a receipt
    that cannot arrive."""
    from app.models.notification import NotificationChannel as Channel

    channel = Channel(
        user_id=_user_id(db_session),
        channel_type=ChannelType.TELEGRAM,
        label="tg",
        config_encrypted={"bot_token": "t", "chat_id": "1"},
        is_enabled=True,
    )
    db_session.add(channel)
    db_session.commit()
    db_session.refresh(channel)

    with patch("app.services.notification.telegram.TelegramSender.send") as send:
        send.return_value.ok = True
        send.return_value.error = None
        auth_client.post(f"/api/notifications/channels/{channel.id}/test")

    log = db_session.query(NotificationLog).filter_by(channel_id=channel.id).one()
    assert log.receipt_token is None


# --- the device reports back -------------------------------------------------


def test_reporting_the_token_marks_the_send_delivered(auth_client, client, db_session, monkeypatch):
    monkeypatch.setattr("app.config.settings.VAPID_PRIVATE_KEY", "private-key")
    channel = _channel(db_session, _user_id(db_session))
    _resp, payload = _run_test(auth_client, channel.id)

    # No Authorization header on purpose: a service worker has none.
    assert (
        client.post(
            "/api/notifications/push/receipt", json={"token": payload["receipt"]}
        ).status_code
        == 204
    )

    log = db_session.query(NotificationLog).filter_by(channel_id=channel.id).one()
    assert log.delivered_at is not None


def test_the_token_is_single_use(auth_client, client, db_session, monkeypatch):
    """Cleared on redemption, so a captured receipt cannot be replayed to make
    a later, undelivered alert look delivered."""
    monkeypatch.setattr("app.config.settings.VAPID_PRIVATE_KEY", "private-key")
    channel = _channel(db_session, _user_id(db_session))
    _resp, payload = _run_test(auth_client, channel.id)
    token = payload["receipt"]

    client.post("/api/notifications/push/receipt", json={"token": token})
    log = db_session.query(NotificationLog).filter_by(channel_id=channel.id).one()
    first = log.delivered_at

    client.post("/api/notifications/push/receipt", json={"token": token})
    db_session.refresh(log)

    assert log.receipt_token is None
    assert log.delivered_at == first, "a replay must not move the timestamp"


def test_an_unknown_token_is_answered_the_same_way_as_a_real_one(client):
    """Answering differently would turn this into an oracle for guessing
    tokens. There is nothing useful to say to the caller either way -- the
    service worker cannot act on the answer."""
    assert client.post("/api/notifications/push/receipt", json={"token": "nope"}).status_code == 204


def test_a_missing_token_is_rejected_rather_than_silently_accepted(client):
    assert client.post("/api/notifications/push/receipt", json={}).status_code == 422


def test_the_receipt_endpoint_needs_no_login(client):
    """The whole point: a service worker has no session. If this ever starts
    requiring auth, every receipt silently stops arriving and the test button
    goes back to saying 未確認 forever."""
    resp = client.post("/api/notifications/push/receipt", json={"token": "anything"})

    assert resp.status_code != 401


# --- reading the result ------------------------------------------------------


def test_the_log_can_be_polled_for_its_delivery_state(auth_client, client, db_session, monkeypatch):
    monkeypatch.setattr("app.config.settings.VAPID_PRIVATE_KEY", "private-key")
    channel = _channel(db_session, _user_id(db_session))
    resp, payload = _run_test(auth_client, channel.id)
    log_id = resp.json()["log_id"]

    before = auth_client.get(f"/api/notifications/logs/{log_id}").json()
    assert before["delivered_at"] is None

    client.post("/api/notifications/push/receipt", json={"token": payload["receipt"]})

    after = auth_client.get(f"/api/notifications/logs/{log_id}").json()
    assert after["delivered_at"] is not None


def test_the_log_endpoint_never_leaks_the_token(auth_client, db_session, monkeypatch):
    """It is a bearer credential for one confirmation. Putting it in a response
    the browser caches would hand it to anything that can read that cache."""
    monkeypatch.setattr("app.config.settings.VAPID_PRIVATE_KEY", "private-key")
    channel = _channel(db_session, _user_id(db_session))
    resp, payload = _run_test(auth_client, channel.id)

    body = auth_client.get(f"/api/notifications/logs/{resp.json()['log_id']}").text

    assert payload["receipt"] not in body


def test_one_owner_cannot_read_another_owners_log(auth_client, client, db_session, monkeypatch):
    monkeypatch.setattr("app.config.settings.VAPID_PRIVATE_KEY", "private-key")
    channel = _channel(db_session, _user_id(db_session))
    resp, _payload = _run_test(auth_client, channel.id)
    log_id = resp.json()["log_id"]

    log = db_session.get(NotificationLog, log_id)
    log.user_id = log.user_id + 1
    db_session.commit()

    assert auth_client.get(f"/api/notifications/logs/{log_id}").status_code == 404


def test_the_log_endpoint_needs_a_login(client):
    assert client.get("/api/notifications/logs/1").status_code == 401
