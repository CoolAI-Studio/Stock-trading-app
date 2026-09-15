"""Seeing what TradingView actually sent.

The audit rows have been written on every authenticated call since the webhook
existed, and pruned on a schedule -- so the data was created and then deleted
without anybody ever having been able to read it. When an alert did not turn
into an order, the owner had no way to tell whether it arrived at all, whether
the secret was wrong, whether the JSON was malformed, or whether a risk gate
refused it.

The endpoint deliberately returns failures too, since those are the rows
somebody is looking for.
"""

from app.models.user import User
from app.models.webhook import TradingViewWebhookLog


def _log(db_session, **kw) -> TradingViewWebhookLog:
    defaults = dict(
        remote_ip="52.89.214.238",
        signature_valid=True,
        raw_body='{"symbol": "2330.TW", "action": "buy"}',
        parsed_ok=True,
    )
    defaults.update(kw)
    log = TradingViewWebhookLog(**defaults)
    db_session.add(log)
    db_session.commit()
    db_session.refresh(log)
    return log


def test_the_owner_can_read_what_arrived(auth_client, db_session):
    user = db_session.query(User).first()
    _log(db_session, user_id=user.id)

    body = auth_client.get("/api/webhooks/tradingview/logs").json()
    assert len(body) == 1
    assert "2330.TW" in body[0]["raw_body"]


def test_a_rejected_call_is_the_row_somebody_is_actually_looking_for(auth_client, db_session):
    """A wrong secret or malformed JSON is precisely the case the owner is
    trying to diagnose, so filtering failures out would defeat the feature."""
    _log(db_session, signature_valid=False, parsed_ok=False, error="secret mismatch")

    body = auth_client.get("/api/webhooks/tradingview/logs").json()
    assert len(body) == 1
    assert body[0]["signature_valid"] is False
    assert "secret" in body[0]["error"]


def test_newest_first_because_that_is_what_someone_is_debugging(auth_client, db_session):
    first = _log(db_session)
    second = _log(db_session)

    body = auth_client.get("/api/webhooks/tradingview/logs").json()
    assert [row["id"] for row in body] == [second.id, first.id]


def test_the_log_is_paged(auth_client, db_session):
    for _ in range(5):
        _log(db_session)

    body = auth_client.get("/api/webhooks/tradingview/logs?limit=2&offset=0").json()
    assert len(body) == 2


def test_the_log_needs_a_login(client):
    assert client.get("/api/webhooks/tradingview/logs").status_code == 401


def test_the_setup_details_are_served_so_nobody_has_to_read_the_source(auth_client):
    """Nothing told the owner what URL to paste into TradingView or that the
    message needs an `id` field, which is the only thing standing between the
    endpoint and a replay.

    The URL is the account's own now (#115), so the message carries no password
    at all -- that half lives in test_a_tradingview_url_is_its_own_credential.py."""
    body = auth_client.get("/api/webhooks/tradingview/setup").json()
    assert "/api/webhooks/tradingview/" in body["url"]
    assert '"id"' in body["example_message"]
    assert body["notes"]


def test_the_setup_endpoint_does_not_hand_out_the_secret(auth_client, monkeypatch):
    """Printing the real shared secret into a response would put it in every
    browser cache and screenshot.

    This used to check that the example showed a placeholder, which was a proxy
    for the property. The personal URL is DERIVED from the secret (#115), so the
    property itself is now the thing to check: the secret's value appears
    nowhere in what is served -- not in the URL, the example, or the notes."""
    monkeypatch.setattr("app.config.settings.TV_WEBHOOK_SECRET", "the-real-shared-secret-value")

    raw = auth_client.get("/api/webhooks/tradingview/setup").text

    assert "the-real-shared-secret-value" not in raw
