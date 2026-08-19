"""Replaying a captured TradingView alert.

The `id` field is what makes an alert idempotent, and nothing required it or
told anyone it existed. Without one, anybody who got hold of a single valid
alert body -- from a log, an intermediate host, a screenshot -- could post it
again as many times as they liked, each time producing a real pending order
carrying the owner's real secret.

Two layers, because they cover different attackers:

- With an `id`, the order is exactly idempotent: the same alert can never
  create a second order, ever.
- Without one, an identical body inside a short window is refused. That
  protects the alerts already configured out there without anyone having to
  change them, and it is honest about its limit -- a patient attacker
  replaying hourly is not stopped by it, which is why the page pushes `id`.
"""

from app.config import settings
from app.models.order import Order
from app.models.user import User
from app.models.webhook import TradingViewWebhookLog


def _alert(**kw) -> dict:
    body = {
        "secret": settings.TV_WEBHOOK_SECRET,
        "symbol": "2330.TW",
        "action": "buy",
        "quantity": 1000,
        "price": 1000,
    }
    body.update(kw)
    return body


def _post(client, body: dict):
    return client.post("/api/webhooks/tradingview", json=body)


def _orders(db_session) -> int:
    return db_session.query(Order).count()


def test_the_same_alert_id_only_ever_creates_one_order(auth_client, client, db_session):
    body = _alert(id="alert-42")

    assert _post(client, body).status_code == 202
    first = _orders(db_session)
    assert first == 1

    _post(client, body)
    assert _orders(db_session) == first, "an id is a promise that this is the same alert"


def test_a_different_id_is_a_different_alert(auth_client, client, db_session):
    # Different symbols, because create_pending_order separately refuses a
    # second pending order for the same symbol and side -- a real guard, but
    # not the one under test here.
    _post(client, _alert(id="alert-1", symbol="2330.TW"))
    _post(client, _alert(id="alert-2", symbol="2317.TW"))

    assert _orders(db_session) == 2


def test_an_identical_body_without_an_id_is_refused_as_a_replay(auth_client, client, db_session):
    """Protects the alerts already configured out there, without anybody
    having to go and change them."""
    body = _alert()

    assert _post(client, body).status_code == 202
    assert _orders(db_session) == 1

    _post(client, body)
    assert _orders(db_session) == 1


def test_a_genuinely_different_alert_still_gets_through(auth_client, client, db_session):
    """The window must not swallow real signals: a different body is a
    different alert even with no id."""
    _post(client, _alert(symbol="2330.TW", price=1000))
    _post(client, _alert(symbol="2317.TW", price=200))

    assert _orders(db_session) == 2


def test_the_replay_is_recorded_so_it_can_be_seen(auth_client, client, db_session):
    body = _alert()
    _post(client, body)
    _post(client, body)

    logs = db_session.query(TradingViewWebhookLog).all()
    assert len(logs) == 2, "both calls are audited; the second is what somebody is looking for"
    assert any("重複" in (log.error or "") for log in logs)


def test_an_alert_with_no_id_is_flagged_even_when_it_works(auth_client, client, db_session):
    """The owner should learn that their alerts are only partly protected,
    from the page rather than from being replayed."""
    _post(client, _alert())

    log = db_session.query(TradingViewWebhookLog).one()
    assert log.order_id is not None
    assert log.missing_id is True


def test_an_alert_with_an_id_is_not_flagged(auth_client, client, db_session):
    _post(client, _alert(id="alert-9"))

    log = db_session.query(TradingViewWebhookLog).one()
    assert log.missing_id is False


def test_a_wrong_secret_is_still_rejected_before_any_of_this(client, db_session):
    resp = _post(client, _alert(secret="not-the-secret", id="x"))
    assert resp.status_code == 401
    assert db_session.query(User).count() == 0 or _orders(db_session) == 0
