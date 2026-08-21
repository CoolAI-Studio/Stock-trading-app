"""Turning the AI on, checking it works, and turning it off -- from inside the app.

AI was the only secret in this codebase that lived in an environment variable.
Telegram tokens, LINE tokens, SMTP passwords and broker credentials are all
stored in the database, Fernet-encrypted, and managed on a page with a 測試
button. AI was not, and the consequences were the ones that pattern exists to
avoid:

  Nothing in the app said the feature existed. Somebody who deployed and later
  wanted it had no way to find out it was there.
  Adding it meant Render's Environment page, which the app never mentions.
  CHANGING it meant a redeploy -- Render restarts the service on every
  environment change, so correcting a typo in a model name cost a minute of
  downtime on the alerting product whose whole promise is not going down.
  There was no way to tell a working key from a wrong one except by asking a
  question and reading the error.

So it moves to where every other credential already is, with the same rules.

PRECEDENCE, and why the environment variable stays. A deployment that already
set AI_API_KEY keeps working untouched -- the row is an override, not a
replacement. That also keeps the blueprint's optional fields meaningful for
somebody who prefers to configure everything in one place.

WHAT IS NOT BUILT: usage metering. The provider's own dashboard counts tokens
far better than this could, and a number here would be a second, worse copy.
What the app owes the owner is that the key is theirs, that every question
spends it, and a switch to stop.
"""

from app.models.user import User
from app.services import ai_settings


def _user(db_session) -> User:
    """The owner. Created here when the test did not go through auth_client --
    db_session alone starts empty, and a None user made every failure read as
    「AttributeError: NoneType has no attribute id」 rather than as whatever the
    test was about."""
    user = db_session.query(User).first()
    if user is None:
        user = User(email="ai@example.com", hashed_password="x")
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    return user


def _save(db_session, user, **kw):
    payload = {
        "provider": "openai_compatible",
        "base_url": "https://x/v1",
        "model": "m",
        "api_key": "sk-abc",
    }
    payload.update(kw)
    return ai_settings.save(db_session, user.id, **payload)


# --- where the answer comes from ---------------------------------------------


def test_a_saved_row_is_what_gets_used(db_session, monkeypatch):
    monkeypatch.setattr("app.config.settings.AI_API_KEY", "sk-from-env")
    user = _user(db_session)
    _save(db_session, user, api_key="sk-from-the-page")

    assert ai_settings.resolve(db_session, user.id).api_key == "sk-from-the-page"


def test_the_environment_variable_still_works_when_nobody_saved_a_row(db_session, monkeypatch):
    """A deployment that already set AI_API_KEY keeps working untouched. The
    row is an override, not a replacement."""
    monkeypatch.setattr("app.config.settings.AI_API_KEY", "sk-from-env")
    monkeypatch.setattr("app.config.settings.AI_MODEL", "env-model")

    resolved = ai_settings.resolve(db_session, _user(db_session).id)

    assert resolved.api_key == "sk-from-env"
    assert resolved.model == "env-model"


def test_neither_means_no_assistant(db_session, monkeypatch):
    monkeypatch.setattr("app.config.settings.AI_API_KEY", "")
    monkeypatch.setattr("app.config.settings.AI_MODEL", "")

    assert not ai_settings.resolve(db_session, _user(db_session).id).is_configured


def test_deleting_the_row_falls_back_rather_than_switching_it_off(db_session, monkeypatch):
    """Delete means 「stop overriding」. Somebody who set a row on a deployment
    that also has an env key expects the env key back, not silence."""
    monkeypatch.setattr("app.config.settings.AI_API_KEY", "sk-from-env")
    monkeypatch.setattr("app.config.settings.AI_MODEL", "env-model")
    user = _user(db_session)
    _save(db_session, user, api_key="sk-from-the-page")

    ai_settings.clear(db_session, user.id)

    assert ai_settings.resolve(db_session, user.id).api_key == "sk-from-env"


def test_one_row_per_user_rather_than_a_pile(db_session):
    user = _user(db_session)
    _save(db_session, user, model="first")
    _save(db_session, user, model="second")

    assert ai_settings.resolve(db_session, user.id).model == "second"


# --- the key is a secret and is treated like one ------------------------------


def test_the_key_is_encrypted_at_rest(db_session):
    """Same rule every other credential in this app follows. A key in
    cleartext in the database is a key in every backup of it."""
    from sqlalchemy import text

    user = _user(db_session)
    _save(db_session, user, api_key="sk-super-secret")

    stored = db_session.execute(text("SELECT api_key_encrypted FROM ai_settings")).scalar()

    assert "sk-super-secret" not in str(stored)


def test_the_api_never_hands_the_key_back(auth_client, db_session):
    """It is write-only, like every other secret on every other settings page:
    a response the browser caches is the wrong place for it."""
    _save(db_session, _user(db_session), api_key="sk-super-secret")

    body = auth_client.get("/api/ai-settings").json()

    assert "sk-super-secret" not in str(body)


def test_but_it_shows_enough_to_recognise_which_key_it_is(auth_client, db_session):
    """A masked tail is what lets somebody tell 「the key I meant」 from 「a key
    I pasted wrong six months ago」 without revealing it."""
    _save(db_session, _user(db_session), api_key="sk-abcdefghijklmnop")

    preview = auth_client.get("/api/ai-settings").json()["key_preview"]

    assert preview and "mnop" in preview
    assert "abcdefgh" not in preview


# --- managing it from the page ------------------------------------------------


def test_the_page_says_whether_anything_is_configured_at_all(auth_client, monkeypatch):
    monkeypatch.setattr("app.config.settings.AI_API_KEY", "")
    monkeypatch.setattr("app.config.settings.AI_MODEL", "")

    assert auth_client.get("/api/ai-settings").json()["configured"] is False


def test_the_page_says_where_the_current_setting_came_from(auth_client, db_session, monkeypatch):
    """「It works and I did not set it here」 is confusing enough to send
    somebody hunting through Render for a value they never typed."""
    monkeypatch.setattr("app.config.settings.AI_API_KEY", "sk-from-env")
    monkeypatch.setattr("app.config.settings.AI_MODEL", "env-model")

    assert auth_client.get("/api/ai-settings").json()["source"] == "env"

    _save(db_session, _user(db_session))
    assert auth_client.get("/api/ai-settings").json()["source"] == "database"


def test_saving_from_the_page_takes_effect_without_a_redeploy(auth_client, db_session):
    """The reason this moved out of the environment at all. Changing an env var
    on Render restarts the service, so fixing a typo in a model name cost a
    minute of downtime on the product whose promise is not going down."""
    resp = auth_client.put(
        "/api/ai-settings",
        json={
            "provider": "openai_compatible",
            "base_url": "https://openrouter.ai/api/v1",
            "model": "some/model",
            "api_key": "sk-typed-just-now",
        },
    )

    assert resp.status_code == 200
    assert ai_settings.resolve(db_session, _user(db_session).id).model == "some/model"


def test_the_model_can_be_changed_without_retyping_the_key(auth_client, db_session):
    """Correcting a model name is the commonest edit and it must not require
    fetching a secret out of somebody's password manager."""
    _save(db_session, _user(db_session), api_key="sk-original", model="old")

    auth_client.put(
        "/api/ai-settings",
        json={
            "provider": "openai_compatible",
            "base_url": "https://x/v1",
            "model": "new",
            "api_key": None,
        },
    )

    resolved = ai_settings.resolve(db_session, _user(db_session).id)
    assert resolved.model == "new"
    assert resolved.api_key == "sk-original"


def test_turning_it_off_is_possible(auth_client, db_session, monkeypatch):
    monkeypatch.setattr("app.config.settings.AI_API_KEY", "")
    _save(db_session, _user(db_session))

    assert auth_client.delete("/api/ai-settings").status_code == 204
    assert not ai_settings.resolve(db_session, _user(db_session).id).is_configured


def test_an_unknown_provider_is_refused(auth_client):
    resp = auth_client.put(
        "/api/ai-settings",
        json={"provider": "my-own-thing", "base_url": "https://x", "model": "m", "api_key": "k"},
    )

    assert resp.status_code == 422


# --- checking it actually works ------------------------------------------------


def test_there_is_a_way_to_find_out_whether_the_key_works(auth_client, db_session):
    """Without it the only test is to ask a real question somewhere else in the
    app and read the error -- which is how somebody concludes the app is broken
    rather than their key."""
    from unittest.mock import patch

    from app.services.ai_provider import AIResult

    _save(db_session, _user(db_session))
    with patch(
        "app.api.routers.ai_settings.get_ai_provider",
        return_value=type(
            "_P", (), {"ask": staticmethod(lambda *a, **k: AIResult(ok=True, reply="hello"))}
        )(),
    ):
        body = auth_client.post("/api/ai-settings/test").json()

    assert body["ok"] is True


def test_a_failing_test_reports_the_provider_s_own_reason(auth_client, db_session):
    from unittest.mock import patch

    from app.services.ai_provider import AIResult

    _save(db_session, _user(db_session))
    with patch(
        "app.api.routers.ai_settings.get_ai_provider",
        return_value=type(
            "_P",
            (),
            {"ask": staticmethod(lambda *a, **k: AIResult(ok=False, error="HTTP 401：金鑰不對"))},
        )(),
    ):
        body = auth_client.post("/api/ai-settings/test").json()

    assert body["ok"] is False
    assert "401" in body["error"]


def test_testing_with_nothing_configured_says_that_rather_than_calling_out(
    auth_client, monkeypatch
):
    monkeypatch.setattr("app.config.settings.AI_API_KEY", "")
    monkeypatch.setattr("app.config.settings.AI_MODEL", "")

    body = auth_client.post("/api/ai-settings/test").json()

    assert body["ok"] is False


# --- and every AI feature uses the resolved settings ---------------------------


def test_the_assistant_uses_the_key_saved_on_the_page(auth_client, db_session, monkeypatch):
    """The point of the whole exercise. A key that is saved and then ignored by
    the features it was saved for is worse than no page at all."""
    monkeypatch.setattr("app.config.settings.AI_API_KEY", "")
    monkeypatch.setattr("app.config.settings.AI_MODEL", "")
    _save(db_session, _user(db_session), api_key="sk-from-the-page", model="page-model")

    assert auth_client.get("/api/system/status").json()["assistant_available"] is True


def test_no_key_anywhere_means_the_assistant_is_not_advertised(auth_client, monkeypatch):
    monkeypatch.setattr("app.config.settings.AI_API_KEY", "")
    monkeypatch.setattr("app.config.settings.AI_MODEL", "")

    assert auth_client.get("/api/system/status").json()["assistant_available"] is False


def test_it_needs_a_login(client):
    assert client.get("/api/ai-settings").status_code == 401


def test_saving_needs_a_login(client):
    assert client.put("/api/ai-settings", json={}).status_code == 401


def test_deleting_needs_a_login(client):
    # Separate rather than parametrized: TestClient.delete() takes no `json`,
    # so one call shape cannot cover both.
    assert client.delete("/api/ai-settings").status_code == 401
