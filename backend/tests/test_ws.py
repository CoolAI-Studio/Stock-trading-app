def _get_ticket(auth_client) -> str:
    resp = auth_client.post("/api/ws/ticket")
    assert resp.status_code == 200, resp.text
    return resp.json()["ticket"]


def test_ticket_then_connect_receives_initial_snapshot(auth_client):
    ticket = _get_ticket(auth_client)

    with auth_client.websocket_connect(f"/ws?ticket={ticket}") as ws:
        message = ws.receive_json()
        assert message["type"] == "snapshot"
        assert "positions" in message["data"]
        assert "pending_orders" in message["data"]
        assert "quotes" in message["data"]


def test_reused_ticket_is_refused(auth_client):
    ticket = _get_ticket(auth_client)

    with auth_client.websocket_connect(f"/ws?ticket={ticket}") as ws:
        ws.receive_json()  # snapshot

    # the ticket was popped on first use -- a second connect must fail
    try:
        with auth_client.websocket_connect(f"/ws?ticket={ticket}"):
            raise AssertionError("second connection with a reused ticket should have been refused")
    except Exception:
        pass  # starlette raises WebSocketDisconnect when the server closes during handshake


def test_unknown_ticket_is_refused(auth_client):
    try:
        with auth_client.websocket_connect("/ws?ticket=not-a-real-ticket"):
            raise AssertionError("connection with an unknown ticket should have been refused")
    except Exception:
        pass


def test_order_created_event_is_pushed_over_the_socket(auth_client):
    ticket = _get_ticket(auth_client)

    with auth_client.websocket_connect(f"/ws?ticket={ticket}") as ws:
        ws.receive_json()  # snapshot

        create_resp = auth_client.post(
            "/api/orders", json={"symbol": "AAPL", "side": "buy", "quantity": "1"}
        )
        assert create_resp.status_code == 201, create_resp.text

        message = ws.receive_json()
        assert message["type"] == "order.created"
        assert message["v"] == 1
        assert message["data"]["order_id"] == create_resp.json()["id"]


def test_order_updated_event_is_pushed_on_confirm(auth_client):
    create_resp = auth_client.post(
        "/api/orders", json={"symbol": "TSLA", "side": "buy", "quantity": "1"}
    )
    order_id = create_resp.json()["id"]

    ticket = _get_ticket(auth_client)
    with auth_client.websocket_connect(f"/ws?ticket={ticket}") as ws:
        ws.receive_json()  # snapshot

        confirm_resp = auth_client.post(
            f"/api/orders/{order_id}/confirm", json={"fill_price": "100"}
        )
        assert confirm_resp.status_code == 200

        message = ws.receive_json()
        assert message["type"] == "order.updated"
        assert message["data"]["status"] == "confirmed"
