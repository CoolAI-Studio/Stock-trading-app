from unittest.mock import MagicMock, patch


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


def test_test_endpoint_sends_and_logs(auth_client):
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
