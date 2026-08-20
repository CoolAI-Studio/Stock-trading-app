from unittest.mock import MagicMock, patch

import httpx
import pytest


@pytest.fixture
def notifications_on(client, monkeypatch):
    """conftest mutes notifications suite-wide; the 測試 endpoint now refuses to
    run while muted, so the tests that exercise it have to say so."""
    monkeypatch.setattr("app.config.settings.NOTIFICATIONS_ENABLED", True)


def test_create_telegram_channel_and_secret_is_never_returned(auth_client):
    resp = auth_client.post(
        "/api/notifications/channels",
        json={
            "channel_type": "telegram",
            "label": "phone",
            "config": {"bot_token": "123456:AAAA-super-secret-token", "chat_id": "999"},
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert "config" not in body
    assert "config_encrypted" not in body
    assert "AAAA-super-secret-token" not in resp.text
    assert body["config_preview"]  # masked preview is present


def test_create_channel_rejects_wrong_shaped_config(auth_client):
    resp = auth_client.post(
        "/api/notifications/channels",
        json={"channel_type": "telegram", "label": "bad", "config": {"only_this": "field"}},
    )
    assert resp.status_code == 422


def test_list_channels_never_leaks_secrets(auth_client):
    auth_client.post(
        "/api/notifications/channels",
        json={
            "channel_type": "email",
            "label": "mail",
            "config": {
                "host": "smtp.example.com",
                "from_addr": "bot@example.com",
                "to_addr": "me@example.com",
                "password": "super-secret-password",
            },
        },
    )

    resp = auth_client.get("/api/notifications/channels")
    assert resp.status_code == 200
    assert "super-secret-password" not in resp.text


def test_update_channel_label_and_toggle(auth_client):
    create_resp = auth_client.post(
        "/api/notifications/channels",
        json={
            "channel_type": "telegram",
            "label": "phone",
            "config": {"bot_token": "t", "chat_id": "1"},
        },
    )
    channel_id = create_resp.json()["id"]

    resp = auth_client.patch(
        f"/api/notifications/channels/{channel_id}", json={"label": "renamed", "is_enabled": False}
    )
    assert resp.status_code == 200
    assert resp.json()["label"] == "renamed"
    assert resp.json()["is_enabled"] is False


def test_delete_channel(auth_client):
    create_resp = auth_client.post(
        "/api/notifications/channels",
        json={
            "channel_type": "telegram",
            "label": "phone",
            "config": {"bot_token": "t", "chat_id": "1"},
        },
    )
    channel_id = create_resp.json()["id"]

    resp = auth_client.delete(f"/api/notifications/channels/{channel_id}")
    assert resp.status_code == 204

    list_resp = auth_client.get("/api/notifications/channels")
    assert list_resp.json() == []


def test_test_endpoint_sends_and_logs(notifications_on, auth_client):
    create_resp = auth_client.post(
        "/api/notifications/channels",
        json={
            "channel_type": "telegram",
            "label": "phone",
            "config": {"bot_token": "t", "chat_id": "1"},
        },
    )
    channel_id = create_resp.json()["id"]

    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"ok": True}
    fake_response.raise_for_status.return_value = None
    with patch("httpx.post", return_value=fake_response):
        resp = auth_client.post(f"/api/notifications/channels/{channel_id}/test")

    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    logs_resp = auth_client.get("/api/notifications/logs")
    assert len(logs_resp.json()) == 1
    assert logs_resp.json()[0]["event"] == "test"


def test_notifications_require_auth(client):
    resp = client.get("/api/notifications/channels")
    assert resp.status_code == 401


def test_create_web_push_channel(auth_client):
    resp = auth_client.post(
        "/api/notifications/channels",
        json={
            "channel_type": "web_push",
            "label": "my-laptop",
            "config": {
                "endpoint": "https://push.example.com/subscription/xyz",
                "p256dh": "some-p256dh-key",
                "auth": "some-auth-secret",
            },
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "some-auth-secret" not in resp.text
    assert body["config_preview"]


def test_vapid_public_key_endpoint(auth_client, monkeypatch):
    monkeypatch.setattr("app.config.settings.VAPID_PUBLIC_KEY", "test-public-key")
    resp = auth_client.get("/api/notifications/push/vapid-public-key")
    assert resp.status_code == 200
    assert resp.json() == {"public_key": "test-public-key"}


def test_vapid_public_key_requires_auth(client):
    resp = client.get("/api/notifications/push/vapid-public-key")
    assert resp.status_code == 401


def test_a_rejected_telegram_send_never_leaks_the_bot_token_through_the_api(
    notifications_on, auth_client
):
    """A wrong/revoked bot token is the most likely Telegram failure there is,
    and httpx names the full request URL -- token included -- in the error it
    raises for it. That string is persisted in NotificationChannel.last_error
    and NotificationLog.error and handed straight back by these two endpoints,
    so a leak here would publish the credential the encrypted config column
    exists to protect."""
    bot_token = "7654321:AAHthis-bot-token-must-never-be-echoed"
    create_resp = auth_client.post(
        "/api/notifications/channels",
        json={
            "channel_type": "telegram",
            "label": "phone",
            "config": {"bot_token": bot_token, "chat_id": "999"},
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    channel_id = create_resp.json()["id"]

    request = httpx.Request("POST", f"https://api.telegram.org/bot{bot_token}/sendMessage")
    unauthorized = httpx.Response(
        401,
        json={"ok": False, "error_code": 401, "description": "Unauthorized"},
        request=request,
    )
    with patch("httpx.post", return_value=unauthorized):
        test_resp = auth_client.post(f"/api/notifications/channels/{channel_id}/test")

    assert test_resp.status_code == 200
    assert test_resp.json()["ok"] is False
    assert bot_token not in test_resp.text

    channels_resp = auth_client.get("/api/notifications/channels")
    logs_resp = auth_client.get("/api/notifications/logs")
    assert bot_token not in channels_resp.text
    assert bot_token not in logs_resp.text
    # The failure is still reported -- the point is a usable error, not a
    # blank one.
    assert "401" in logs_resp.json()[0]["error"]
