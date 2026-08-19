from decimal import Decimal


def test_list_positions_excludes_zero_quantity(auth_client):
    create_resp = auth_client.post(
        "/api/orders", json={"symbol": "AAPL", "side": "buy", "quantity": "5"}
    )
    order_id = create_resp.json()["id"]
    auth_client.post(f"/api/orders/{order_id}/confirm", json={"fill_price": "100"})

    resp = auth_client.get("/api/positions")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["symbol"] == "AAPL"


def test_get_missing_position_is_404(auth_client):
    resp = auth_client.get("/api/positions/NFLX")
    assert resp.status_code == 404


def test_adjust_position_creates_or_updates(auth_client):
    resp = auth_client.patch(
        "/api/positions/AAPL", json={"quantity": "3", "avg_entry_price": "200"}
    )
    assert resp.status_code == 200, resp.text
    assert Decimal(resp.json()["quantity"]) == Decimal(3)


def test_flatten_position(auth_client):
    auth_client.patch("/api/positions/AAPL", json={"quantity": "3", "avg_entry_price": "200"})

    resp = auth_client.delete("/api/positions/AAPL")
    assert resp.status_code == 204

    get_resp = auth_client.get("/api/positions/AAPL")
    assert Decimal(get_resp.json()["quantity"]) == Decimal(0)


def test_positions_require_auth(client):
    resp = client.get("/api/positions")
    assert resp.status_code == 401


def test_adjust_rejects_a_negative_quantity(auth_client):
    """A negative quantity used to be accepted, and the next confirmed buy then
    silently reset avg_entry_price to 0 -- corrupting the cost basis every
    later P&L calculation and stop-loss comparison depends on."""
    resp = auth_client.patch(
        "/api/positions/AAPL", json={"quantity": "-5", "avg_entry_price": "100"}
    )
    assert resp.status_code == 422


def test_adjust_rejects_a_negative_avg_entry_price(auth_client):
    resp = auth_client.patch(
        "/api/positions/AAPL", json={"quantity": "5", "avg_entry_price": "-100"}
    )
    assert resp.status_code == 422


def test_adjust_still_accepts_zero_to_flatten(auth_client):
    resp = auth_client.patch("/api/positions/AAPL", json={"quantity": "0", "avg_entry_price": "0"})
    assert resp.status_code == 200
