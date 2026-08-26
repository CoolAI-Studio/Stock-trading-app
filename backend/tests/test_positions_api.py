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


# --- a position has to say what it is worth now -----------------------------
#
# The page showed cost and realized P&L only, so the one question the owner
# actually opens it to ask -- am I up or down right now -- could only be
# answered by copying a price off the dashboard and subtracting by hand. The
# quote is already in the database: the worker writes market_quotes every poll
# and the stop-loss scan reads it on the same tick.


def _hold(auth_client, symbol: str, quantity: str, cost: str) -> None:
    resp = auth_client.patch(
        f"/api/positions/{symbol}", json={"quantity": quantity, "avg_entry_price": cost}
    )
    assert resp.status_code == 200, resp.text


def _quote(db_session, symbol: str, price: str) -> None:
    from app.enums import DataSource
    from app.models.market import MarketQuote
    from app.models.mixins import utcnow

    db_session.add(
        MarketQuote(
            symbol=symbol,
            data_source=DataSource.YFINANCE,
            price=Decimal(price),
            quote_time=utcnow(),
        )
    )
    db_session.commit()


def test_a_position_reports_its_value_and_unrealized_pnl(auth_client, db_session):
    _hold(auth_client, "2330.TW", "1000", "1000")
    _quote(db_session, "2330.TW", "1050")

    body = auth_client.get("/api/positions").json()
    assert len(body) == 1
    row = body[0]
    assert Decimal(row["current_price"]) == Decimal(1050)
    assert Decimal(row["market_value"]) == Decimal(1050000)
    assert Decimal(row["unrealized_pnl"]) == Decimal(50000)
    assert Decimal(row["unrealized_pnl_pct"]) == Decimal(5)


def test_a_losing_position_reports_a_negative_unrealized_pnl(auth_client, db_session):
    _hold(auth_client, "AAPL", "10", "200")
    _quote(db_session, "AAPL", "180")

    row = auth_client.get("/api/positions").json()[0]
    assert Decimal(row["unrealized_pnl"]) == Decimal(-200)
    assert Decimal(row["unrealized_pnl_pct"]) == Decimal(-10)


def test_a_position_with_no_quote_yet_reports_nothing_rather_than_zero(auth_client):
    """Zero would read as "flat", which is a different and much more
    reassuring statement than "the price feed has not reached this symbol"."""
    _hold(auth_client, "NOQUOTE", "10", "200")

    row = auth_client.get("/api/positions").json()[0]
    assert row["current_price"] is None
    assert row["market_value"] is None
    assert row["unrealized_pnl"] is None
    assert row["unrealized_pnl_pct"] is None


def test_the_quote_timestamp_comes_along_so_a_stale_price_can_be_spotted(auth_client, db_session):
    """A price with no time on it looks equally current whether it arrived a
    second ago or last Friday before the feed broke."""
    _hold(auth_client, "AAPL", "10", "200")
    _quote(db_session, "AAPL", "180")

    row = auth_client.get("/api/positions").json()[0]
    assert row["quote_time"].endswith(("Z", "+00:00"))
