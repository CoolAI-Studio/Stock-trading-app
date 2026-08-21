import pytest

from app.core import login_throttle


@pytest.fixture(autouse=True)
def _clean_throttle_state():
    # The throttle is deliberately process-global, so failed logins in one test
    # would otherwise count against the next one.
    login_throttle.reset_all()


class _FakeClock:
    """Stands in for the `time` module inside login_throttle. A real lockout
    short enough to sleep through would be shorter than the login requests
    themselves -- one bcrypt verify already costs ~0.3s."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


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


def test_register_rejected_when_even_the_first_account_is_closed(client, monkeypatch):
    """A deployment that wants its account created by hand can refuse even the
    first one.

    The rule CHANGED: registration used to be gated solely on
    ALLOW_REGISTRATION, so an empty deployment with the flag off refused
    everybody -- which is why DEPLOYMENT.md told the owner to switch it on,
    curl an account into existence, and switch it back. Now the door closes
    itself once an owner exists (see test_registration_closes_itself.py), and
    the flags only decide whether the FIRST account may be made from the web
    page at all. Both must be off to refuse it.
    """
    monkeypatch.setattr("app.config.settings.ALLOW_REGISTRATION", False)
    monkeypatch.setattr("app.config.settings.ALLOW_FIRST_ACCOUNT", False)

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


def test_change_password_with_wrong_current_password_is_rejected(auth_client):
    resp = auth_client.post(
        "/api/auth/change-password",
        json={"current_password": "not-the-real-one", "new_password": "a-brand-new-passphrase"},
    )
    assert resp.status_code == 401

    still_works = auth_client.post(
        "/api/auth/login",
        data={"username": "fixture-user@example.com", "password": "correct-horse-battery"},
    )
    assert still_works.status_code == 200


def test_change_password_succeeds_and_retires_the_old_password(auth_client):
    resp = auth_client.post(
        "/api/auth/change-password",
        json={
            "current_password": "correct-horse-battery",
            "new_password": "a-brand-new-passphrase",
        },
    )
    assert resp.status_code == 204, resp.text

    old = auth_client.post(
        "/api/auth/login",
        data={"username": "fixture-user@example.com", "password": "correct-horse-battery"},
    )
    assert old.status_code == 401

    new = auth_client.post(
        "/api/auth/login",
        data={"username": "fixture-user@example.com", "password": "a-brand-new-passphrase"},
    )
    assert new.status_code == 200


def test_change_password_requires_authentication(client):
    resp = client.post(
        "/api/auth/change-password",
        json={
            "current_password": "correct-horse-battery",
            "new_password": "a-brand-new-passphrase",
        },
    )
    assert resp.status_code == 401


def test_change_password_rejects_a_new_password_over_72_bytes(auth_client):
    resp = auth_client.post(
        "/api/auth/change-password",
        json={"current_password": "correct-horse-battery", "new_password": "x" * 73},
    )
    assert resp.status_code == 422


def test_repeated_failed_logins_lock_the_account(client, monkeypatch):
    monkeypatch.setattr("app.config.settings.ALLOW_REGISTRATION", True)
    monkeypatch.setattr("app.config.settings.LOGIN_MAX_FAILED_ATTEMPTS", 3)
    client.post(
        "/api/auth/register",
        json={"email": "lockme@example.com", "password": "correct-horse-battery"},
    )

    for _ in range(3):
        resp = client.post(
            "/api/auth/login",
            data={"username": "lockme@example.com", "password": "wrong-password"},
        )
        assert resp.status_code == 401

    # Even the right password is refused now -- that is the whole point.
    locked = client.post(
        "/api/auth/login",
        data={"username": "lockme@example.com", "password": "correct-horse-battery"},
    )
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0


def test_lockout_clears_once_it_expires(client, monkeypatch):
    monkeypatch.setattr("app.config.settings.ALLOW_REGISTRATION", True)
    monkeypatch.setattr("app.config.settings.LOGIN_MAX_FAILED_ATTEMPTS", 3)
    monkeypatch.setattr("app.config.settings.LOGIN_LOCKOUT_MINUTES", 15.0)
    clock = _FakeClock()
    monkeypatch.setattr("app.core.login_throttle.time", clock)
    client.post(
        "/api/auth/register",
        json={"email": "unlockme@example.com", "password": "correct-horse-battery"},
    )

    for _ in range(3):
        client.post(
            "/api/auth/login",
            data={"username": "unlockme@example.com", "password": "wrong-password"},
        )
    assert (
        client.post(
            "/api/auth/login",
            data={"username": "unlockme@example.com", "password": "correct-horse-battery"},
        ).status_code
        == 429
    )

    clock.now += 15 * 60 + 1

    resp = client.post(
        "/api/auth/login",
        data={"username": "unlockme@example.com", "password": "correct-horse-battery"},
    )
    assert resp.status_code == 200, resp.text


def test_successful_login_resets_the_failure_counter(client, monkeypatch):
    monkeypatch.setattr("app.config.settings.ALLOW_REGISTRATION", True)
    monkeypatch.setattr("app.config.settings.LOGIN_MAX_FAILED_ATTEMPTS", 3)
    client.post(
        "/api/auth/register",
        json={"email": "resetme@example.com", "password": "correct-horse-battery"},
    )

    for _ in range(2):
        client.post(
            "/api/auth/login",
            data={"username": "resetme@example.com", "password": "wrong-password"},
        )
    assert (
        client.post(
            "/api/auth/login",
            data={"username": "resetme@example.com", "password": "correct-horse-battery"},
        ).status_code
        == 200
    )

    # Without a reset these two would reach the 3-failure threshold.
    for _ in range(2):
        resp = client.post(
            "/api/auth/login",
            data={"username": "resetme@example.com", "password": "wrong-password"},
        )
        assert resp.status_code == 401
