MA5_SOURCE = """
class Strategy:
    def __init__(self):
        self.name = "AAPL_MA5_Trend"
        self.symbol = "AAPL"
        self.prices = []

    def on_tick(self, current_price: float) -> str:
        self.prices.append(current_price)
        if len(self.prices) < 5:
            return "HOLD"
        ma5 = sum(self.prices[-5:]) / 5
        return "BUY" if current_price > ma5 else "HOLD"
"""

BROKEN_SOURCE = "def not_a_strategy(:\n    pass"


def test_validate_accepts_well_formed_strategy(auth_client):
    resp = auth_client.post("/api/strategies/validate", json={"source_code": MA5_SOURCE})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["detected_name"] == "AAPL_MA5_Trend"
    assert body["detected_symbol"] == "AAPL"
    assert body["sample_signals"]


def test_validate_reports_clean_error_for_broken_code(auth_client):
    resp = auth_client.post("/api/strategies/validate", json={"source_code": BROKEN_SOURCE})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["error"]


def test_validate_requires_auth(client):
    resp = client.post("/api/strategies/validate", json={"source_code": MA5_SOURCE})
    assert resp.status_code == 401


def test_create_then_list_then_get_strategy(auth_client):
    create_resp = auth_client.post(
        "/api/strategies",
        json={"name": "my-ma5", "symbol": "AAPL", "source_code": MA5_SOURCE},
    )
    assert create_resp.status_code == 201, create_resp.text
    strategy_id = create_resp.json()["id"]
    assert create_resp.json()["is_active"] is False

    list_resp = auth_client.get("/api/strategies")
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1

    get_resp = auth_client.get(f"/api/strategies/{strategy_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["name"] == "my-ma5"


def test_create_rejects_broken_strategy_code(auth_client):
    resp = auth_client.post(
        "/api/strategies",
        json={"name": "bad", "symbol": "AAPL", "source_code": BROKEN_SOURCE},
    )
    assert resp.status_code == 422


def test_duplicate_name_for_same_user_is_rejected(auth_client):
    payload = {"name": "dup", "symbol": "AAPL", "source_code": MA5_SOURCE}
    first = auth_client.post("/api/strategies", json=payload)
    assert first.status_code == 201

    second = auth_client.post("/api/strategies", json=payload)
    assert second.status_code == 409


def test_activate_and_deactivate_strategy(auth_client):
    create_resp = auth_client.post(
        "/api/strategies",
        json={"name": "toggle-me", "symbol": "AAPL", "source_code": MA5_SOURCE},
    )
    strategy_id = create_resp.json()["id"]

    activate_resp = auth_client.post(f"/api/strategies/{strategy_id}/activate")
    assert activate_resp.status_code == 200
    assert activate_resp.json()["is_active"] is True

    deactivate_resp = auth_client.post(f"/api/strategies/{strategy_id}/deactivate")
    assert deactivate_resp.status_code == 200
    assert deactivate_resp.json()["is_active"] is False


def test_delete_strategy(auth_client):
    create_resp = auth_client.post(
        "/api/strategies",
        json={"name": "delete-me", "symbol": "AAPL", "source_code": MA5_SOURCE},
    )
    strategy_id = create_resp.json()["id"]

    delete_resp = auth_client.delete(f"/api/strategies/{strategy_id}")
    assert delete_resp.status_code == 204

    get_resp = auth_client.get(f"/api/strategies/{strategy_id}")
    assert get_resp.status_code == 404


def test_cannot_access_another_users_strategy(auth_client, client, monkeypatch):
    create_resp = auth_client.post(
        "/api/strategies",
        json={"name": "mine", "symbol": "AAPL", "source_code": MA5_SOURCE},
    )
    strategy_id = create_resp.json()["id"]

    monkeypatch.setattr("app.config.settings.ALLOW_REGISTRATION", True)
    client.post(
        "/api/auth/register",
        json={"email": "other@example.com", "password": "correct-horse-battery"},
    )
    login_resp = client.post(
        "/api/auth/login",
        data={"username": "other@example.com", "password": "correct-horse-battery"},
    )
    other_token = login_resp.json()["access_token"]

    resp = client.get(
        f"/api/strategies/{strategy_id}", headers={"Authorization": f"Bearer {other_token}"}
    )
    assert resp.status_code == 404


def test_list_samples_returns_ma5_sample(auth_client):
    resp = auth_client.get("/api/strategies/samples")
    assert resp.status_code == 200
    names = [s["filename"] for s in resp.json()]
    assert "ma5_cross.py" in names


def test_get_one_strategy_returns_its_source_code(auth_client):
    """The edit form prefills from this. It used to omit source_code, so the
    editor opened blank -- indistinguishable from the code having been lost,
    and saving from that state would have wiped it for real."""
    created = auth_client.post(
        "/api/strategies",
        json={"name": "prefill-me", "symbol": "AAPL", "source_code": MA5_SOURCE},
    )
    strategy_id = created.json()["id"]

    resp = auth_client.get(f"/api/strategies/{strategy_id}")
    assert resp.status_code == 200
    assert resp.json()["source_code"] == MA5_SOURCE


def test_listing_strategies_still_omits_source_code(auth_client):
    """Kept out of the list on purpose: the dashboard polls it, and shipping
    every strategy's full source on each poll is wasted bytes."""
    auth_client.post(
        "/api/strategies",
        json={"name": "in-a-list", "symbol": "AAPL", "source_code": MA5_SOURCE},
    )

    resp = auth_client.get("/api/strategies")
    assert resp.status_code == 200
    assert "source_code" not in resp.json()[0]
