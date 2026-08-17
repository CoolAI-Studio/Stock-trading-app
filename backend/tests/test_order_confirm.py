from decimal import Decimal


def test_create_manual_order_then_confirm(auth_client):
    create_resp = auth_client.post(
        "/api/orders", json={"symbol": "AAPL", "side": "buy", "quantity": "10"}
    )
    assert create_resp.status_code == 201, create_resp.text
    order_id = create_resp.json()["id"]
    assert create_resp.json()["status"] == "pending"

    confirm_resp = auth_client.post(
        f"/api/orders/{order_id}/confirm", json={"fill_price": "150.50"}
    )
    assert confirm_resp.status_code == 200, confirm_resp.text
    body = confirm_resp.json()
    assert body["status"] == "confirmed"
    # Numeric(18, 8) round-trips through SQLite/Postgres with full scale
    # (e.g. "150.50000000") -- compare numerically, not as exact strings.
    assert Decimal(body["fill_price"]) == Decimal("150.5")
    assert body["broker_ref"].startswith("manual:")

    position_resp = auth_client.get("/api/positions/AAPL")
    assert position_resp.status_code == 200
    assert Decimal(position_resp.json()["quantity"]) == Decimal(10)
    assert Decimal(position_resp.json()["avg_entry_price"]) == Decimal("150.5")


def test_confirming_twice_returns_409(auth_client):
    create_resp = auth_client.post(
        "/api/orders", json={"symbol": "AAPL", "side": "buy", "quantity": "1"}
    )
    order_id = create_resp.json()["id"]

    first = auth_client.post(f"/api/orders/{order_id}/confirm", json={"fill_price": "100"})
    assert first.status_code == 200

    second = auth_client.post(f"/api/orders/{order_id}/confirm", json={"fill_price": "100"})
    assert second.status_code == 409


def test_reject_order(auth_client):
    create_resp = auth_client.post(
        "/api/orders", json={"symbol": "AAPL", "side": "buy", "quantity": "1"}
    )
    order_id = create_resp.json()["id"]

    reject_resp = auth_client.post(
        f"/api/orders/{order_id}/reject", json={"reason": "changed my mind"}
    )
    assert reject_resp.status_code == 200
    assert reject_resp.json()["status"] == "rejected"
    assert reject_resp.json()["reject_reason"] == "changed my mind"


def test_rejecting_twice_returns_409(auth_client):
    create_resp = auth_client.post(
        "/api/orders", json={"symbol": "AAPL", "side": "buy", "quantity": "1"}
    )
    order_id = create_resp.json()["id"]

    auth_client.post(f"/api/orders/{order_id}/reject", json={})
    second = auth_client.post(f"/api/orders/{order_id}/reject", json={})
    assert second.status_code == 409


def test_list_orders_filters_by_status(auth_client):
    auth_client.post("/api/orders", json={"symbol": "AAPL", "side": "buy", "quantity": "1"})
    create_resp = auth_client.post(
        "/api/orders", json={"symbol": "TSLA", "side": "buy", "quantity": "1"}
    )
    auth_client.post(f"/api/orders/{create_resp.json()['id']}/reject", json={})

    pending = auth_client.get("/api/orders", params={"status": "pending"})
    assert pending.status_code == 200
    assert len(pending.json()) == 1
    assert pending.json()[0]["symbol"] == "AAPL"

    rejected = auth_client.get("/api/orders", params={"status": "rejected"})
    assert len(rejected.json()) == 1
    assert rejected.json()[0]["symbol"] == "TSLA"


def test_second_manual_buy_for_same_symbol_is_deduped(auth_client):
    payload = {"symbol": "AAPL", "side": "buy", "quantity": "1"}
    first = auth_client.post("/api/orders", json=payload)
    assert first.status_code == 201

    second = auth_client.post("/api/orders", json=payload)
    assert second.status_code == 422


def test_orders_require_auth(client):
    resp = client.get("/api/orders")
    assert resp.status_code == 401


def test_cannot_confirm_another_users_order(auth_client, client, monkeypatch):
    create_resp = auth_client.post(
        "/api/orders", json={"symbol": "AAPL", "side": "buy", "quantity": "1"}
    )
    order_id = create_resp.json()["id"]

    monkeypatch.setattr("app.config.settings.ALLOW_REGISTRATION", True)
    client.post(
        "/api/auth/register",
        json={"email": "other-orders@example.com", "password": "correct-horse-battery"},
    )
    login_resp = client.post(
        "/api/auth/login",
        data={"username": "other-orders@example.com", "password": "correct-horse-battery"},
    )
    other_token = login_resp.json()["access_token"]

    resp = client.post(
        f"/api/orders/{order_id}/confirm",
        json={"fill_price": "100"},
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert resp.status_code == 404


def test_confirming_a_sell_larger_than_the_position_is_rejected(auth_client):
    """The order must stay pending: it used to be committed as CONFIRMED first
    and only then handed to apply_fill, which clamped the excess away -- so the
    order said one quantity and the position another, permanently."""
    auth_client.patch("/api/positions/AAPL", json={"quantity": "10", "avg_entry_price": "100"})

    created = auth_client.post(
        "/api/orders", json={"symbol": "AAPL", "side": "sell", "quantity": "25"}
    )
    order_id = created.json()["id"]

    resp = auth_client.post(f"/api/orders/{order_id}/confirm", json={"fill_price": "150"})
    assert resp.status_code == 422
    assert "25" in resp.json()["detail"]

    still_pending = auth_client.get(f"/api/orders/{order_id}")
    assert still_pending.json()["status"] == "pending"

    unchanged = auth_client.get("/api/positions/AAPL")
    assert unchanged.json()["quantity"] == "10"
    assert unchanged.json()["realized_pnl"] == "0"
