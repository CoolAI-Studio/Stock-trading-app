import json
from datetime import timedelta

from app.api.routers import webhooks
from app.enums import OrderStatus
from app.models.mixins import utcnow
from app.models.order import Order
from app.models.webhook import TradingViewWebhookLog

TV_SECRET = "test-tv-secret"


def _register_user(client, monkeypatch, email="tv@example.com"):
    monkeypatch.setattr("app.config.settings.ALLOW_REGISTRATION", True)
    client.post("/api/auth/register", json={"email": email, "password": "correct-horse-battery"})


def test_good_secret_creates_a_pending_order(client, monkeypatch, db_session):
    monkeypatch.setattr("app.config.settings.TV_WEBHOOK_SECRET", TV_SECRET)
    _register_user(client, monkeypatch)

    body = json.dumps(
        {"secret": TV_SECRET, "symbol": "AAPL", "action": "buy", "quantity": 1, "id": "alert-1"}
    )
    resp = client.post(
        "/api/webhooks/tradingview", content=body, headers={"Content-Type": "text/plain"}
    )

    assert resp.status_code == 202, resp.text
    assert resp.json()["ok"] is True
    assert resp.json()["created"] is True

    order = db_session.query(Order).filter(Order.symbol == "AAPL").first()
    assert order is not None
    assert order.status == OrderStatus.PENDING
    assert order.source == "tradingview"

    log = db_session.query(TradingViewWebhookLog).first()
    assert log is not None
    assert log.signature_valid is True
    assert log.parsed_ok is True
    assert log.order_id == order.id


def test_bad_secret_is_rejected_without_writing_anything(client, monkeypatch, db_session):
    """The endpoint is public and its path is guessable, so a request that
    fails the secret must cost the database nothing. Writing the audit row
    first let a stranger in a loop append 8KB at a time until Neon's 0.5GB
    free tier was full -- and a full database fails every write in the app."""
    monkeypatch.setattr("app.config.settings.TV_WEBHOOK_SECRET", TV_SECRET)
    _register_user(client, monkeypatch)

    body = json.dumps({"secret": "wrong-secret", "symbol": "AAPL", "action": "buy"})
    resp = client.post(
        "/api/webhooks/tradingview", content=body, headers={"Content-Type": "text/plain"}
    )

    assert resp.status_code == 401
    assert db_session.query(TradingViewWebhookLog).count() == 0
    assert db_session.query(Order).count() == 0


def test_malformed_json_returns_200_not_a_retry_status(client, monkeypatch, db_session):
    monkeypatch.setattr("app.config.settings.TV_WEBHOOK_SECRET", TV_SECRET)
    _register_user(client, monkeypatch)

    resp = client.post(
        "/api/webhooks/tradingview",
        content="not valid json {{{",
        headers={"Content-Type": "text/plain"},
    )

    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    # No row: the secret travels inside the JSON, so a body that will not
    # parse is a body that cannot be authenticated -- and unauthenticated
    # requests never reach the database.
    assert db_session.query(TradingViewWebhookLog).count() == 0


def test_invalid_payload_shape_returns_200_and_is_logged(client, monkeypatch, db_session):
    monkeypatch.setattr("app.config.settings.TV_WEBHOOK_SECRET", TV_SECRET)
    _register_user(client, monkeypatch)

    # valid JSON, correct secret, but missing required fields / bad action
    body = json.dumps({"secret": TV_SECRET, "symbol": "AAPL", "action": "hold"})
    resp = client.post(
        "/api/webhooks/tradingview", content=body, headers={"Content-Type": "text/plain"}
    )

    assert resp.status_code == 200
    assert resp.json()["ok"] is False

    log = db_session.query(TradingViewWebhookLog).first()
    assert log.signature_valid is True
    assert log.parsed_ok is False


def test_duplicate_alert_id_does_not_create_a_second_order(client, monkeypatch, db_session):
    monkeypatch.setattr("app.config.settings.TV_WEBHOOK_SECRET", TV_SECRET)
    _register_user(client, monkeypatch)

    body = json.dumps(
        {"secret": TV_SECRET, "symbol": "AAPL", "action": "buy", "quantity": 1, "id": "dup-alert"}
    )
    first = client.post(
        "/api/webhooks/tradingview", content=body, headers={"Content-Type": "text/plain"}
    )
    second = client.post(
        "/api/webhooks/tradingview", content=body, headers={"Content-Type": "text/plain"}
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["created"] is False

    assert db_session.query(Order).filter(Order.symbol == "AAPL").count() == 1


def test_no_registered_user_returns_200_and_logs_the_gap(client, monkeypatch, db_session):
    monkeypatch.setattr("app.config.settings.TV_WEBHOOK_SECRET", TV_SECRET)
    # deliberately skip registering a user

    body = json.dumps({"secret": TV_SECRET, "symbol": "AAPL", "action": "buy", "quantity": 1})
    resp = client.post(
        "/api/webhooks/tradingview", content=body, headers={"Content-Type": "text/plain"}
    )

    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert db_session.query(Order).count() == 0


# ---- the audit table cannot be used as free storage --------------------------


def test_an_oversized_body_is_refused_and_never_stored(client, monkeypatch, db_session):
    monkeypatch.setattr("app.config.settings.TV_WEBHOOK_SECRET", TV_SECRET)
    _register_user(client, monkeypatch)

    oversized = json.dumps(
        {"secret": TV_SECRET, "symbol": "AAPL", "action": "buy", "pad": "x" * 200_000}
    )
    resp = client.post(
        "/api/webhooks/tradingview", content=oversized, headers={"Content-Type": "text/plain"}
    )

    assert resp.status_code == 413
    assert db_session.query(TradingViewWebhookLog).count() == 0
    assert db_session.query(Order).count() == 0


def test_an_oversized_body_is_refused_even_without_a_content_length(
    client, monkeypatch, db_session
):
    """A chunked request declares no length, so the limit cannot be a header
    check alone -- the body has to be counted as it arrives."""
    monkeypatch.setattr("app.config.settings.TV_WEBHOOK_SECRET", TV_SECRET)
    _register_user(client, monkeypatch)

    def _chunks():
        yield b'{"secret": "' + TV_SECRET.encode() + b'", "pad": "'
        for _ in range(40):
            yield b"x" * 8192
        yield b'"}'

    resp = client.post(
        "/api/webhooks/tradingview", content=_chunks(), headers={"Content-Type": "text/plain"}
    )

    assert resp.status_code == 413
    assert db_session.query(TradingViewWebhookLog).count() == 0


def test_the_stored_audit_row_does_not_quote_the_secret_back(client, monkeypatch, db_session):
    """The shared secret is a bearer credential. An audit row repeating it is
    a second, unencrypted copy of the password guarding this endpoint."""
    monkeypatch.setattr("app.config.settings.TV_WEBHOOK_SECRET", TV_SECRET)
    _register_user(client, monkeypatch)

    body = json.dumps(
        {"secret": TV_SECRET, "symbol": "AAPL", "action": "buy", "quantity": 1, "id": "keep-me"}
    )
    resp = client.post(
        "/api/webhooks/tradingview", content=body, headers={"Content-Type": "text/plain"}
    )

    assert resp.status_code == 202
    log = db_session.query(TradingViewWebhookLog).one()
    assert TV_SECRET not in log.raw_body
    # Everything with audit value survives -- a redaction, not a purge.
    assert "AAPL" in log.raw_body
    assert "keep-me" in log.raw_body


def test_the_audit_log_is_capped_so_it_cannot_grow_without_bound(client, monkeypatch, db_session):
    monkeypatch.setattr("app.config.settings.TV_WEBHOOK_SECRET", TV_SECRET)
    monkeypatch.setattr(webhooks, "_LOG_MAX_ROWS", 3)
    _register_user(client, monkeypatch)

    for n in range(6):
        body = json.dumps(
            {"secret": TV_SECRET, "symbol": "AAPL", "action": "buy", "id": f"alert-{n}"}
        )
        client.post(
            "/api/webhooks/tradingview", content=body, headers={"Content-Type": "text/plain"}
        )

    logs = db_session.query(TradingViewWebhookLog).order_by(TradingViewWebhookLog.id).all()
    assert len(logs) == 3
    # The rows kept are the newest -- recent history is the useful part.
    assert "alert-5" in logs[-1].raw_body


def test_audit_rows_older_than_the_retention_window_are_dropped(client, monkeypatch, db_session):
    monkeypatch.setattr("app.config.settings.TV_WEBHOOK_SECRET", TV_SECRET)
    _register_user(client, monkeypatch)

    ancient = TradingViewWebhookLog(raw_body='{"symbol": "OLD"}', signature_valid=True)
    db_session.add(ancient)
    db_session.commit()
    ancient.received_at = utcnow() - timedelta(days=webhooks._LOG_RETENTION_DAYS + 1)
    db_session.commit()

    body = json.dumps({"secret": TV_SECRET, "symbol": "AAPL", "action": "buy", "id": "fresh"})
    client.post("/api/webhooks/tradingview", content=body, headers={"Content-Type": "text/plain"})

    remaining = db_session.query(TradingViewWebhookLog).all()
    assert len(remaining) == 1
    assert "AAPL" in remaining[0].raw_body
