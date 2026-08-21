"""The blanks a first-time deployer gets wrong, and the order they meet them in.

render.yaml presents seven values as a flat parallel list. They are not
parallel -- three of them are a chain, and a stranger cannot see the chain:

    Neon → DATABASE_URL → deploy the backend → its URL is PUBLIC_BASE_URL
    → deploy the frontend pointing at that URL → the frontend's URL is
    CORS_ORIGINS, which goes back into the backend.

Two of those are circular by nature: you cannot know a URL before the thing
exists. What can be fixed is who has to carry the value across.

THREE CHANGES HERE, in the order of how much they cost somebody.

1. PUBLIC_BASE_URL derives itself. Render injects RENDER_EXTERNAL_URL (the
   service's own https://...onrender.com address, confirmed against Render's
   docs), so nobody has to copy the URL back into the service it came from.
   Only as a FALLBACK: an explicit value still wins, because a custom domain
   is exactly the case Render's variable does not know about.

2. CORS_ORIGINS and PUBLIC_BASE_URL are reported by the setup page. Neither
   stops the app booting, so neither was mentioned anywhere -- and they are
   the two a first-timer actually gets wrong. Reported apart from the blocking
   ones, because 「the app will not start」 and 「TradingView will send to the
   wrong address」 are not the same urgency and a page that mixes them teaches
   people to skim.

3. The setup endpoints answer any origin. THIS ONE IS THE TRAP: a wrong
   CORS_ORIGINS means the browser discards every response from this backend,
   INCLUDING the setup page's own -- so the page that exists to explain the
   problem is blanked by the problem. The endpoints carry no secrets, no user
   data and no credentials, so opening them costs nothing and is the only way
   the explanation survives the thing it is explaining.
"""

from app.config import Settings
from app.services import setup_state


def _fernet_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


def _settings(**overrides) -> Settings:
    values = {
        "JWT_SECRET": "a" * 48,
        "TV_WEBHOOK_SECRET": "b" * 48,
        "SECRET_ENCRYPTION_KEY": _fernet_key(),
        "DATABASE_URL": "postgresql://user:pw@host/db",
    }
    values.update(overrides)
    return Settings(**values)


def _advisory(s: Settings) -> dict[str, setup_state.MissingSetting]:
    return {item.name: item for item in setup_state.missing_settings(s) if not item.blocking}


# --- 1. the URL that names itself --------------------------------------------


def test_the_public_url_comes_from_render_when_nobody_set_one(monkeypatch):
    """Render injects the service's own address. Making a person copy it back
    into the service it came from is a step that exists for no reason."""
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://my-app.onrender.com")

    assert _settings().public_base_url == "https://my-app.onrender.com"


def test_an_explicit_value_still_wins(monkeypatch):
    """A custom domain is exactly the case Render's variable does not know
    about, so this can only ever be a fallback."""
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://my-app.onrender.com")

    assert _settings(PUBLIC_BASE_URL="https://alerts.example.com").public_base_url == (
        "https://alerts.example.com"
    )


def test_local_development_is_unaffected(monkeypatch):
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)

    assert _settings().public_base_url == "http://localhost:8000"


def test_the_webhook_panel_shows_the_derived_url(auth_client, monkeypatch):
    """The one place the value is read. A wrong address here is a TradingView
    webhook that never arrives, with nothing on screen explaining why."""
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://my-app.onrender.com")
    monkeypatch.setattr("app.config.settings.PUBLIC_BASE_URL", "http://localhost:8000")

    url = auth_client.get("/api/webhooks/tradingview/setup").json()["url"]

    assert url.startswith("https://my-app.onrender.com")


# --- 2. the two nobody was told about ----------------------------------------


def test_a_deployment_still_on_the_localhost_default_is_told(monkeypatch):
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)

    assert "PUBLIC_BASE_URL" in _advisory(_settings(PUBLIC_BASE_URL="http://localhost:8000"))


def test_cors_still_pointing_at_localhost_is_told(monkeypatch):
    """The single most likely mistake: the frontend is deployed, its URL was
    never copied back, and every page loads blank with a console error the
    owner will not open."""
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)

    assert "CORS_ORIGINS" in _advisory(_settings(CORS_ORIGINS="http://localhost:5173"))


def test_neither_of_them_stops_the_app_from_starting(monkeypatch):
    """They are wrong, not fatal. Treating them as fatal would lock somebody
    out of a deployment that works."""
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    settings = _settings(CORS_ORIGINS="http://localhost:5173")

    assert not any(item.blocking for item in _advisory(settings).values())
    assert setup_state.blocking_settings(settings) == []


def test_a_configured_pair_is_not_nagged_about(monkeypatch):
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    settings = _settings(
        CORS_ORIGINS="https://my-app.vercel.app",
        PUBLIC_BASE_URL="https://my-app.onrender.com",
    )

    assert _advisory(settings) == {}


def test_the_blocking_ones_are_still_blocking():
    assert all(item.blocking for item in setup_state.blocking_settings(_settings(JWT_SECRET="")))


def test_every_entry_says_which_step_of_the_flow_it_belongs_to():
    """Seven parallel blanks is what render.yaml already gave them. The order
    is the part that was missing."""
    for item in setup_state.missing_settings(_settings(JWT_SECRET="", CORS_ORIGINS="")):
        assert item.step >= 1, item


def test_the_database_comes_before_the_urls():
    """Nothing works without it, and the URLs cannot even be known until the
    services exist."""
    items = {i.name: i.step for i in setup_state.missing_settings(_settings(DATABASE_URL=""))}

    assert items["DATABASE_URL"] < items["CORS_ORIGINS"]


# --- 3. the trap: the page blanked by the problem it explains -----------------


def test_the_setup_page_answers_a_frontend_on_an_unlisted_origin(client, monkeypatch):
    """A wrong CORS_ORIGINS makes the browser discard every response from this
    backend -- including this page's. Without this the explanation is blanked
    by the very thing it is explaining."""
    monkeypatch.setattr("app.config.settings.SECRET_ENCRYPTION_KEY", "")
    monkeypatch.setattr("app.config.settings.CORS_ORIGINS", "https://someone-else.example.com")

    resp = client.get("/api/setup/status", headers={"Origin": "https://my-app.vercel.app"})

    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "https://my-app.vercel.app"


def test_an_ordinary_endpoint_is_not_opened_up(auth_client, monkeypatch):
    """Only the setup endpoints. They carry no secrets, no user data and no
    credentials; everything else carries all three."""
    monkeypatch.setattr("app.config.settings.CORS_ORIGINS", "https://someone-else.example.com")

    resp = auth_client.get("/api/positions", headers={"Origin": "https://my-app.vercel.app"})

    assert resp.headers.get("access-control-allow-origin") != "https://my-app.vercel.app"


def test_the_preflight_for_the_setup_page_is_answered_too(client, monkeypatch):
    """A cross-origin GET with no custom headers needs no preflight, but the
    browser sends one for the POST that generates a key."""
    monkeypatch.setattr("app.config.settings.SECRET_ENCRYPTION_KEY", "")

    resp = client.options(
        "/api/setup/generate",
        headers={
            "Origin": "https://my-app.vercel.app",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert resp.status_code < 400, resp.status_code
    assert resp.headers.get("access-control-allow-origin") == "https://my-app.vercel.app"
