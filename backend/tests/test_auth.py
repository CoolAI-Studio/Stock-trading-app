def test_register_then_login_then_me(client, monkeypatch):
    monkeypatch.setattr("app.config.settings.ALLOW_REGISTRATION", True)

    register_resp = client.post(
        "/api/auth/register",
        json={"email": "trader@example.com", "password": "correct-horse-battery"},
    )
    assert register_resp.status_code == 201, register_resp.text

    login_resp = client.post(
        "/api/auth/login",
        data={"username": "trader@example.com", "password": "correct-horse-battery"},
    )
    assert login_resp.status_code == 200, login_resp.text
    token = login_resp.json()["access_token"]

    me_resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 200
    assert me_resp.json()["email"] == "trader@example.com"


def test_register_rejected_when_registration_closed(client, monkeypatch):
    monkeypatch.setattr("app.config.settings.ALLOW_REGISTRATION", False)

    resp = client.post(
        "/api/auth/register",
        json={"email": "nope@example.com", "password": "correct-horse-battery"},
    )
    assert resp.status_code == 403


def test_login_with_wrong_password_is_rejected(client, monkeypatch):
    monkeypatch.setattr("app.config.settings.ALLOW_REGISTRATION", True)
    client.post(
        "/api/auth/register",
        json={"email": "trader2@example.com", "password": "correct-horse-battery"},
    )

    resp = client.post(
        "/api/auth/login",
        data={"username": "trader2@example.com", "password": "wrong-password"},
    )
    assert resp.status_code == 401


def test_me_without_token_is_rejected(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_password_over_72_bytes_is_rejected(client, monkeypatch):
    monkeypatch.setattr("app.config.settings.ALLOW_REGISTRATION", True)
    resp = client.post(
        "/api/auth/register",
        json={"email": "long@example.com", "password": "x" * 73},
    )
    assert resp.status_code == 422
