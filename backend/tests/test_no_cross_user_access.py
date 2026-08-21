"""One account must never see another account's anything.

The owner asked to be certain: 「我目前的這些資料都只屬於我，並不會在給其他使用者
上看到，請檢查是否會洩漏」.

WHY THIS FILE EXISTS AS WELL AS THE SCHEMA. Every user-owned table carries a
user_id -- checked, all of them. But a foreign key does not filter anything; the
QUERY does. A single `.filter(Model.id == item_id)` that forgets
`Model.user_id == user.id` hands one account another's row, and it looks
completely ordinary in review. Reading the routers is how you convince yourself
there is no such line; this file is how you find out.

So this is empirical rather than by inspection: two real accounts, real tokens,
and account B is pointed at every one of account A's resources in turn. Read,
update, delete. The correct answer is 404 or 403 -- never 200 with A's data, and
never a successful write.

WHY B IS CREATED DIRECTLY IN THE DATABASE. Registration now closes itself once a
deployment has an owner (test_registration_closes_itself.py), so a second
account cannot be made through the API any more. That is the fix, not the
subject: this file asks what is true IF a second account exists -- which it can,
via scripts/create_user.py, or on any deployment where ALLOW_REGISTRATION was
left switched on before that fix landed. The owner's data has to be safe in that
world too.
"""

import pytest

from app.core.security import create_access_token, hash_password
from app.models.user import User

OTHER = "intruder@example.com"


@pytest.fixture
def intruder(auth_client, db_session):
    """A second account, and a client carrying its token.

    Built on the same TestClient because the app is shared; the header is
    swapped per request via the returned helper rather than globally, so the
    owner's own client keeps working alongside it.
    """
    user = User(email=OTHER, hashed_password=hash_password("a different password"))
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token(subject=str(user.id), token_version=user.token_version)
    return {"Authorization": f"Bearer {token}"}


def _owned(auth_client):
    """One row of every user-owned kind, created by the owner.

    Returns {label: (path, payload_for_update)}. A resource this app cannot
    create through the API is exercised through its read route only.
    """
    created: dict[str, tuple[str, dict]] = {}

    strategy = auth_client.post(
        "/api/strategies",
        json={
            "name": "owner-only",
            "symbol": "2330.TW",
            "data_source": "yfinance",
            "source_code": (
                "class Strategy:\n"
                "    def __init__(self):\n"
                "        self.name = 'owner-only'\n"
                "        self.symbol = '2330.TW'\n"
                "        self.timeframe = '1d'\n"
                "\n"
                "    def on_bar(self, bar) -> str:\n"
                "        return 'BUY' if bar.close > bar.open else 'HOLD'\n"
            ),
        },
    )
    assert strategy.status_code in (200, 201), strategy.text
    created["strategy"] = (f"/api/strategies/{strategy.json()['id']}", {"name": "stolen"})

    watch = auth_client.post("/api/watchlist", json={"symbol": "0050.TW"})
    assert watch.status_code in (200, 201), watch.text

    return created


def test_the_two_accounts_are_really_different(auth_client, intruder, db_session):
    """A test that accidentally reuses one identity would pass every assertion
    below while proving nothing."""
    assert db_session.query(User).count() == 2

    me = auth_client.get("/api/auth/me").json()
    them = auth_client.get("/api/auth/me", headers=intruder).json()

    assert me["email"] != them["email"]
    assert them["email"] == OTHER


# --- reading ----------------------------------------------------------------------


def test_the_intruder_cannot_read_the_owners_strategy(auth_client, intruder):
    path, _ = _owned(auth_client)["strategy"]

    resp = auth_client.get(path, headers=intruder)

    assert resp.status_code in (403, 404), resp.text


def test_the_intruder_sees_an_empty_list_not_the_owners(auth_client, intruder):
    """List routes are the quieter half of the same bug: no id in the path, so
    nothing looks like it needs an ownership check."""
    _owned(auth_client)

    for path in (
        "/api/strategies",
        "/api/watchlist",
        "/api/orders",
        "/api/positions",
        "/api/backtests",
        "/api/notifications/channels",
        "/api/alerts",
    ):
        resp = auth_client.get(path, headers=intruder)
        assert resp.status_code in (200, 404), f"{path}: {resp.status_code}"
        if resp.status_code == 200:
            body = resp.json()
            rows = body if isinstance(body, list) else body.get("items", body.get("results", []))
            assert rows == [], f"{path} leaked {len(rows)} of the owner's rows"


def test_the_intruder_cannot_read_the_owners_ai_key(auth_client, intruder):
    """The AI settings row is per user and holds an API key the owner pays for.
    A deployment-wide singleton here would be a leak even with every table
    scoped correctly."""
    saved = auth_client.put(
        "/api/ai-settings",
        json={
            "provider": "anthropic",
            "base_url": "https://api.anthropic.com",
            "model": "claude-opus-5",
            "api_key": "sk-owner-secret-value",
        },
    )
    # NOT skipped on failure. A skip here would let the one test that guards a
    # credential the owner PAYS FOR go quietly green-ish forever the day the
    # schema changes -- which is exactly what happened the first time this was
    # written, over a missing base_url.
    assert saved.status_code in (200, 201), saved.text

    resp = auth_client.get("/api/ai-settings", headers=intruder)

    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        assert "sk-owner-secret-value" not in resp.text
        assert resp.json().get("provider") != "anthropic" or resp.json().get("api_key") in (
            None,
            "",
        )


def test_no_response_to_the_intruder_ever_contains_the_owners_symbol(auth_client, intruder):
    """A blanket sweep rather than a per-field assertion: if 2330.TW appears
    anywhere in anything the intruder is served, something leaked, whatever the
    shape of the response."""
    _owned(auth_client)

    for path in (
        "/api/strategies",
        "/api/watchlist",
        "/api/positions",
        "/api/orders",
        "/api/alerts",
        "/api/backtests",
    ):
        resp = auth_client.get(path, headers=intruder)
        if resp.status_code == 200:
            assert "2330.TW" not in resp.text, f"{path} leaked the owner's symbol"
            assert "0050.TW" not in resp.text, f"{path} leaked the owner's watchlist"


# --- writing ----------------------------------------------------------------------


def test_the_intruder_cannot_edit_the_owners_strategy(auth_client, intruder):
    path, patch = _owned(auth_client)["strategy"]

    resp = auth_client.patch(path, json=patch, headers=intruder)

    assert resp.status_code in (403, 404), resp.text
    assert auth_client.get(path).json()["name"] == "owner-only"


def test_the_intruder_cannot_delete_the_owners_strategy(auth_client, intruder):
    path, _ = _owned(auth_client)["strategy"]

    resp = auth_client.delete(path, headers=intruder)

    assert resp.status_code in (403, 404), resp.text
    assert auth_client.get(path).status_code == 200, "the owner's strategy was deleted"


def test_the_intruder_cannot_delete_from_the_owners_watchlist(auth_client, intruder):
    _owned(auth_client)

    resp = auth_client.delete("/api/watchlist/0050.TW", headers=intruder)

    assert resp.status_code in (403, 404), resp.text
    assert "0050.TW" in auth_client.get("/api/watchlist").text


# --- the token itself ---------------------------------------------------------------


def test_a_token_signed_with_another_key_is_refused(auth_client):
    """The signing key is per deployment. If a forged token were accepted, every
    deployment's data would be reachable from any other."""
    from jose import jwt

    forged = jwt.encode({"sub": "1", "ver": 0}, "some-other-deployments-key", algorithm="HS256")

    resp = auth_client.get("/api/strategies", headers={"Authorization": f"Bearer {forged}"})

    assert resp.status_code == 401


def test_an_unsigned_token_is_refused(auth_client):
    """alg=none is the oldest JWT trick there is."""
    import base64
    import json

    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    forged = f"{part({'alg': 'none', 'typ': 'JWT'})}.{part({'sub': '1', 'ver': 0})}."

    resp = auth_client.get("/api/strategies", headers={"Authorization": f"Bearer {forged}"})

    assert resp.status_code == 401


def test_no_token_at_all_is_refused(client):
    assert client.get("/api/strategies").status_code == 401
