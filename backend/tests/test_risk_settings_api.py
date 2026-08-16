from decimal import Decimal


def test_get_risk_settings_creates_defaults(auth_client):
    resp = auth_client.get("/api/risk-settings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["max_pending_orders_per_symbol"] == 3
    assert body["signal_cooldown_sec"] == 300


def test_update_risk_settings_partial(auth_client):
    resp = auth_client.put("/api/risk-settings", json={"max_position_qty": "500"})
    assert resp.status_code == 200
    assert Decimal(resp.json()["max_position_qty"]) == Decimal(500)

    # unrelated fields are untouched by a partial update
    assert resp.json()["signal_cooldown_sec"] == 300


def test_risk_settings_require_auth(client):
    resp = client.get("/api/risk-settings")
    assert resp.status_code == 401
