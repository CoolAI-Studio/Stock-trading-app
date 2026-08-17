from unittest.mock import MagicMock, patch


def test_create_credential_and_secret_is_never_returned(auth_client):
    resp = auth_client.post(
        "/api/broker-credentials",
        json={
            "label": "my-yuanta",
            "broker_name": "Yuanta SPARK API",
            "config": {"api_key": "super-secret-key-value", "account_id": "12345"},
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert "config" not in body
    assert "config_encrypted" not in body
    assert "super-secret-key-value" not in resp.text
    assert body["config_preview"]


def test_list_credentials_never_leaks_secrets(auth_client):
    auth_client.post(
        "/api/broker-credentials",
        json={
            "label": "my-firstrade",
            "broker_name": "Firstrade (unofficial)",
            "config": {"session_token": "super-secret-token"},
        },
    )

    resp = auth_client.get("/api/broker-credentials")
    assert resp.status_code == 200
    assert "super-secret-token" not in resp.text


def test_update_credential(auth_client):
    create_resp = auth_client.post(
        "/api/broker-credentials",
        json={"label": "phone", "broker_name": "b", "config": {"key": "v"}},
    )
    credential_id = create_resp.json()["id"]

    resp = auth_client.patch(
        f"/api/broker-credentials/{credential_id}", json={"label": "renamed"}
    )
    assert resp.status_code == 200
    assert resp.json()["label"] == "renamed"


def test_delete_credential(auth_client):
    create_resp = auth_client.post(
        "/api/broker-credentials",
        json={"label": "phone", "broker_name": "b", "config": {"key": "v"}},
    )
    credential_id = create_resp.json()["id"]

    resp = auth_client.delete(f"/api/broker-credentials/{credential_id}")
    assert resp.status_code == 204

    list_resp = auth_client.get("/api/broker-credentials")
    assert list_resp.json() == []


def test_broker_credentials_require_auth(client):
    resp = client.get("/api/broker-credentials")
    assert resp.status_code == 401


def test_ai_assist_success(auth_client, monkeypatch):
    monkeypatch.setattr("app.config.settings.AI_API_KEY", "test-key")
    monkeypatch.setattr("app.config.settings.AI_MODEL", "test-model")
    monkeypatch.setattr("app.config.settings.AI_PROVIDER", "openai_compatible")

    fake_response = MagicMock(status_code=200)
    fake_response.raise_for_status.return_value = None
    fake_response.json.return_value = {
        "choices": [{"message": {"content": "Here's how to find your API key..."}}]
    }
    with patch("httpx.post", return_value=fake_response):
        resp = auth_client.post(
            "/api/broker-credentials/ai-assist",
            json={"message": "How do I get an API key from my broker?"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "API key" in body["reply"]


def test_ai_assist_without_api_key_configured(auth_client, monkeypatch):
    monkeypatch.setattr("app.config.settings.AI_API_KEY", "")
    resp = auth_client.post("/api/broker-credentials/ai-assist", json={"message": "help"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "AI_API_KEY" in body["error"]


def test_ai_assist_requires_auth(client):
    resp = client.post("/api/broker-credentials/ai-assist", json={"message": "help"})
    assert resp.status_code == 401
