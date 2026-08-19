"""Being able to take an account back.

The password guards the broker API keys, the notification tokens and the
ability to place orders, and there was no way to revoke a token that had been
issued. Changing the password did not help: whoever held the old one kept full
access for up to a day, and 登出 only cleared it from that one browser's
storage while the server went on accepting it.

Fixed with a version stamped into each token. Changing the password, or
explicitly signing out everywhere, bumps it and every token minted before that
moment stops being accepted -- which is what makes changing the password an
actual response to "somebody has my account".
"""

from app.core.security import create_access_token, decode_token
from app.models.user import User

# What tests/conftest.py's auth_client fixture registers.
FIXTURE_PASSWORD = "correct-horse-battery"
NEW_PASSWORD = "a-new-password-123"


def _login(client, email: str, password: str) -> str:
    resp = client.post("/api/auth/login", data={"username": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _with(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --- the token carries a version -------------------------------------------


def test_a_token_records_which_version_of_the_account_it_belongs_to():
    token = create_access_token(subject="1", token_version=3)
    subject, version = decode_token(token)
    assert subject == "1"
    assert version == 3


def test_a_token_from_before_versions_existed_is_still_readable():
    """Tokens already in the wild have no version claim. Rejecting them would
    sign the owner out on the deploy that adds this, for no security gain --
    they are indistinguishable from version 0, which is what every account
    starts at."""
    from jose import jwt

    from app.config import settings

    legacy = jwt.encode(
        {"sub": "1", "exp": 9999999999}, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM
    )
    subject, version = decode_token(legacy)
    assert subject == "1"
    assert version == 0


# --- changing the password revokes what was already out --------------------


def test_changing_the_password_stops_the_old_token_working(auth_client, client, db_session):
    user = db_session.query(User).first()
    old_token = create_access_token(subject=str(user.id), token_version=user.token_version)

    assert client.get("/api/positions", headers=_with(old_token)).status_code == 200

    resp = auth_client.post(
        "/api/auth/change-password",
        json={"current_password": FIXTURE_PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert resp.status_code == 204, resp.text

    assert client.get("/api/positions", headers=_with(old_token)).status_code == 401


def test_the_new_password_gets_a_working_token(auth_client, client, db_session):
    user = db_session.query(User).first()
    auth_client.post(
        "/api/auth/change-password",
        json={"current_password": FIXTURE_PASSWORD, "new_password": NEW_PASSWORD},
    )

    fresh = _login(client, user.email, NEW_PASSWORD)
    assert client.get("/api/positions", headers=_with(fresh)).status_code == 200


def test_the_wrong_current_password_changes_nothing(auth_client, client, db_session):
    user = db_session.query(User).first()
    before = user.token_version

    resp = auth_client.post(
        "/api/auth/change-password",
        json={"current_password": "not-my-password", "new_password": NEW_PASSWORD},
    )

    assert resp.status_code == 401
    db_session.refresh(user)
    assert user.token_version == before, "a failed attempt must not sign anyone out"


# --- signing out everywhere ------------------------------------------------


def test_signing_out_everywhere_invalidates_the_token_that_asked(auth_client, client, db_session):
    """Including the caller's own: 登出所有裝置 that leaves the device you are
    holding signed in has not done what it says."""
    user = db_session.query(User).first()
    token = create_access_token(subject=str(user.id), token_version=user.token_version)

    assert auth_client.post("/api/auth/logout-everywhere").status_code == 204
    assert client.get("/api/positions", headers=_with(token)).status_code == 401


def test_signing_out_everywhere_needs_a_login(client):
    assert client.post("/api/auth/logout-everywhere").status_code == 401


# --- the login record ------------------------------------------------------


def test_a_successful_login_is_recorded(auth_client, client, db_session):
    user = db_session.query(User).first()
    _login(client, user.email, FIXTURE_PASSWORD)

    db_session.refresh(user)
    assert user.last_login_at is not None


def test_the_previous_login_is_kept_so_something_can_be_compared(auth_client, client, db_session):
    """ "Last login" showing the login that is happening right now tells the
    owner nothing. The one before it is the useful number."""
    user = db_session.query(User).first()
    _login(client, user.email, FIXTURE_PASSWORD)
    db_session.refresh(user)
    first = user.last_login_at

    _login(client, user.email, FIXTURE_PASSWORD)
    db_session.refresh(user)

    assert user.previous_login_at == first
    assert user.last_login_at >= first


def test_a_failed_login_does_not_move_the_clock(auth_client, client, db_session):
    """The fixture already logged in once, so the interesting assertion is
    that a wrong password leaves the stamp where it was -- otherwise a string
    of failed attempts would erase the last time the owner actually got in."""
    user = db_session.query(User).first()
    before = user.last_login_at
    assert before is not None

    client.post("/api/auth/login", data={"username": user.email, "password": "wrong"})

    db_session.refresh(user)
    assert user.last_login_at == before
    assert user.previous_login_at is None


def test_the_account_page_can_see_when_it_was_last_used(auth_client):
    body = auth_client.get("/api/auth/me").json()
    assert "last_login_at" in body
    assert "previous_login_at" in body


# --- the encryption key has to be there before anything needs it ------------


def test_a_missing_encryption_key_stops_the_app_at_boot():
    """It used to fail only when a secret was first touched -- so a deploy
    that forgot the key came up green, passed its health check, and then threw
    an unexplained 500 the first time the owner tried to save a Telegram
    token or a broker key. Days later, on the one screen that matters."""
    from app.config import Settings, verify_required_secrets

    broken = Settings(
        JWT_SECRET="a" * 50,
        TV_WEBHOOK_SECRET="b" * 50,
        SECRET_ENCRYPTION_KEY="",
    )
    try:
        verify_required_secrets(broken)
    except RuntimeError as exc:
        assert "SECRET_ENCRYPTION_KEY" in str(exc)
        return
    raise AssertionError("expected a RuntimeError")


def test_a_key_that_is_not_a_valid_fernet_key_is_caught_at_boot_too():
    """A non-empty but malformed key passes a presence check and then fails on
    first use, which is the same silent-until-it-matters failure."""
    from app.config import Settings, verify_required_secrets

    broken = Settings(
        JWT_SECRET="a" * 50,
        TV_WEBHOOK_SECRET="b" * 50,
        SECRET_ENCRYPTION_KEY="not-a-fernet-key",
    )
    try:
        verify_required_secrets(broken)
    except RuntimeError as exc:
        assert "SECRET_ENCRYPTION_KEY" in str(exc)
        return
    raise AssertionError("expected a RuntimeError")


def test_a_real_key_passes():
    from cryptography.fernet import Fernet

    from app.config import Settings, verify_required_secrets

    verify_required_secrets(
        Settings(
            JWT_SECRET="a" * 50,
            TV_WEBHOOK_SECRET="b" * 50,
            SECRET_ENCRYPTION_KEY=Fernet.generate_key().decode(),
        )
    )
