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

WEEKLY_BAR_SOURCE = """
class Strategy:
    def __init__(self):
        self.name = "TSMC_weekly"
        self.symbol = "2330.TW"
        self.timeframe = "1wk"
        self.closes = []

    def on_bar(self, bar) -> str:
        self.closes.append(bar.close)
        return "BUY" if bar.close > bar.open else "HOLD"
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
    assert body["entry_point"] == "on_tick"


def test_validate_says_when_a_strategy_runs_on_candles_instead(auth_client):
    """Two entry points now exist and they read almost the same. The owner
    has to be told which one their code actually got, or a strategy that
    silently never runs looks identical to one that works."""
    resp = auth_client.post("/api/strategies/validate", json={"source_code": WEEKLY_BAR_SOURCE})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["entry_point"] == "on_bar"
    assert body["timeframe"] == "1wk"
    assert body["sample_signals"]


def test_a_candle_strategy_can_be_saved_and_activated(auth_client):
    create_resp = auth_client.post(
        "/api/strategies",
        json={"name": "tsmc-weekly", "symbol": "2330.TW", "source_code": WEEKLY_BAR_SOURCE},
    )
    assert create_resp.status_code == 201, create_resp.text
    strategy_id = create_resp.json()["id"]

    activate_resp = auth_client.post(f"/api/strategies/{strategy_id}/activate")
    assert activate_resp.status_code == 200, activate_resp.text
    assert activate_resp.json()["is_active"] is True


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


def test_list_samples_serves_something_loadable(auth_client):
    """Deliberately not pinned to a filename. What the samples are is a
    product decision that has already changed once; what matters here is that
    the 從範例載入 button has something to load and that each entry carries
    the source it claims to. tests/test_sample_strategies.py is where the
    samples themselves are held to a standard."""
    resp = auth_client.get("/api/strategies/samples")
    assert resp.status_code == 200
    samples = resp.json()
    assert samples
    for sample in samples:
        assert sample["filename"].endswith(".py")
        assert "class Strategy" in sample["source_code"]


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


# --- the live instance has to die when the strategy stops ------------------
#
# StrategyRegistry caches a compiled instance per strategy id, which is the
# whole reason an MA5 strategy works at all -- self.prices has to survive
# between ticks. The flip side is that the accumulated state has to be thrown
# away when the strategy stops running, and `invalidate()` existed but was
# never called from anywhere.


def _create(auth_client, name: str = "resume-test") -> int:
    resp = auth_client.post(
        "/api/strategies",
        json={"name": name, "symbol": "AAPL", "source_code": MA5_SOURCE},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_pausing_a_strategy_throws_away_the_prices_it_had_accumulated(auth_client):
    """Otherwise a strategy paused for two weeks resumes with a price series
    that jumps straight from the old prices to today's -- the gap is invisible
    to it, so the first crossing it reports is an artefact of the pause."""
    from app.services.market_loop import _registry

    strategy_id = _create(auth_client)
    running = _registry.get_or_load(strategy_id, MA5_SOURCE)
    running.instance.on_tick(100.0)
    assert running.instance.prices == [100.0]

    auth_client.post(f"/api/strategies/{strategy_id}/deactivate")

    resumed = _registry.get_or_load(strategy_id, MA5_SOURCE)
    assert resumed is not running
    assert resumed.instance.prices == [], "resumed with a fresh price series"


def test_deleting_a_strategy_releases_its_instance(auth_client):
    """A cached instance whose strategy no longer exists is unreachable and
    never evicted -- it just holds memory for the life of the process, on a
    box where the whole app runs in one worker."""
    from app.services.market_loop import _registry

    strategy_id = _create(auth_client, name="delete-test")
    loaded = _registry.get_or_load(strategy_id, MA5_SOURCE)
    assert _registry.is_cached(strategy_id)

    auth_client.delete(f"/api/strategies/{strategy_id}")

    assert not _registry.is_cached(strategy_id)
    assert loaded is not None  # the object itself is fine; the cache entry is gone


def test_editing_the_symbol_restarts_the_strategy_clean(auth_client):
    """Changing the source already recompiles, because the registry keys on a
    content hash. Changing only the *symbol* did not -- the instance kept
    every price it had accumulated for the previous symbol and carried them
    straight into the new one's moving average."""
    from app.services.market_loop import _registry

    strategy_id = _create(auth_client, name="symbol-swap")
    running = _registry.get_or_load(strategy_id, MA5_SOURCE)
    running.instance.on_tick(100.0)

    auth_client.patch(f"/api/strategies/{strategy_id}", json={"symbol": "2330.TW"})

    resumed = _registry.get_or_load(strategy_id, MA5_SOURCE)
    assert resumed.instance.prices == [], "AAPL's prices must not seed 2330.TW's average"
