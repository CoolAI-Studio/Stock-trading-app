import json

from app.models.enums import OrderStatus
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


def test_bad_secret_is_rejected_and_logged(client, monkeypatch, db_session):
    monkeypatch.setattr("app.config.settings.TV_WEBHOOK_SECRET", TV_SECRET)
    _register_user(client, monkeypatch)

    body = json.dumps({"secret": "wrong-secret", "symbol": "AAPL", "action": "buy"})
    resp = client.post(
        "/api/webhooks/tradingview", content=body, headers={"Content-Type": "text/plain"}
    )

    assert resp.status_code == 401

    log = db_session.query(TradingViewWebhookLog).first()
    assert log is not None
    assert log.signature_valid is False

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

    log = db_session.query(TradingViewWebhookLog).first()
    assert log is not None
    assert log.parsed_ok is False


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
