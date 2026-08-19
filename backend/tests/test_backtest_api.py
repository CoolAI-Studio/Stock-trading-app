"""The backtest endpoints: running one, bounding it, and being able to read
an old run back after the strategy it scored has moved on.

Nothing here touches the network -- the market data service is overridden with
a stub provider, the same way tests/test_market_api.py does it.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.main import app
from app.models.backtest import BacktestRun
from app.models.enums import DataSource
from app.services.backtest import MAX_BACKTEST_BARS
from app.services.market_data.base import Bar, Timeframe
from app.services.market_data.service import MarketDataService, get_market_data_service

_START = datetime(2026, 1, 5, tzinfo=UTC)
_END = _START + timedelta(days=39)

DAILY_BUYER = """
class Strategy:
    def __init__(self):
        self.name = "daily_buyer"
        self.symbol = "2330.TW"
        self.timeframe = "1d"
        self.warmup_bars = 2
        self.seen = 0

    def on_bar(self, bar) -> str:
        self.seen += 1
        if self.seen == 3:
            return "BUY"
        if self.seen == 8:
            return "SELL"
        return "HOLD"
"""

WEEKLY_BUYER = DAILY_BUYER.replace('self.timeframe = "1d"', 'self.timeframe = "1wk"').replace(
    'self.name = "daily_buyer"', 'self.name = "weekly_buyer"'
)

# An on_tick strategy declares no candle size -- the live loop drives it from
# quotes -- so the replay candle is the one thing the request gets to choose.
TICK_BUYER = """
class Strategy:
    def __init__(self):
        self.name = "tick_buyer"
        self.symbol = "2330.TW"
        self.prices = []

    def on_tick(self, current_price: float) -> str:
        self.prices.append(current_price)
        return "BUY" if len(self.prices) == 3 else "HOLD"
"""


class _StubBarProvider:
    """A rising daily series for 2330.TW and nothing else, so a symbol the
    provider cannot resolve behaves the way yfinance does: no bars at all."""

    data_source = DataSource.YFINANCE

    def __init__(self, count: int = 40) -> None:
        self.count = count

    def get_quotes(self, symbols):
        return {}

    def get_bars(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Bar]:
        if symbol != "2330.TW":
            return []
        step = timedelta(weeks=1) if timeframe is Timeframe.WEEK_1 else timedelta(days=1)
        bars = [
            Bar(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=_START + step * i,
                open=100.0 + i,
                high=102.0 + i,
                low=99.0 + i,
                close=101.0 + i,
                volume=1000.0,
            )
            for i in range(self.count)
        ]
        return bars[-limit:]


@pytest.fixture
def stub_market_data():
    service = MarketDataService(providers={DataSource.YFINANCE: _StubBarProvider()})
    app.dependency_overrides[get_market_data_service] = lambda: service
    try:
        yield service
    finally:
        app.dependency_overrides.pop(get_market_data_service, None)


def _request(**overrides) -> dict:
    payload = {
        "source_code": DAILY_BUYER,
        "symbol": "2330.TW",
        "start": _START.isoformat(),
        "end": _END.isoformat(),
    }
    payload.update(overrides)
    return payload


def _create_strategy(auth_client, source: str = DAILY_BUYER, name: str = "daily_buyer") -> int:
    resp = auth_client.post(
        "/api/strategies",
        json={"name": name, "symbol": "2330.TW", "source_code": source, "warmup_bars": 2},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# --- running one ------------------------------------------------------------


def test_a_draft_strategy_can_be_backtested_without_being_saved(auth_client, stub_market_data):
    """The point of the draft path: judge the code before committing it to the
    strategies list."""
    resp = auth_client.post("/api/backtests", json=_request())

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["result"]["strategy_name"] == "daily_buyer"
    assert body["result"]["summary"]["trade_count"] == 1
    assert body["result"]["equity_curve"]
    assert body["strategy_id"] is None


def test_the_response_carries_a_chart_worth_of_data(auth_client, stub_market_data):
    resp = auth_client.post("/api/backtests", json=_request())

    result = resp.json()["result"]
    curve = result["equity_curve"]
    assert len(curve) == result["summary"]["bars_tested"]
    assert set(curve[0]) == {"timestamp", "close", "position_qty", "cash", "equity"}
    trade = result["trades"][0]
    assert set(trade) == {
        "opened_at",
        "closed_at",
        "quantity",
        "entry_price",
        "exit_price",
        "pnl",
        "return_pct",
    }
    # Money comes back as a plain fixed-point string, never "0E-8".
    assert "E" not in trade["pnl"]


def test_the_response_states_its_assumptions(auth_client, stub_market_data):
    resp = auth_client.post("/api/backtests", json=_request())

    body = resp.json()
    assert body["result"]["assumption_notes"]
    assert body["assumptions"]["fill_price_basis"] == "next_open"
    assert body["assumptions"]["commission_rate"] == "0.001425"


def test_costs_can_be_set_per_run_and_are_echoed_back(auth_client, stub_market_data):
    resp = auth_client.post(
        "/api/backtests",
        json=_request(
            fill_price_basis="close",
            commission_rate="0.003",
            slippage_rate="0",
            sell_tax_rate="0.003",
            quantity="2",
            initial_capital="50000",
        ),
    )

    assert resp.status_code == 201, resp.text
    assumptions = resp.json()["assumptions"]
    assert assumptions["fill_price_basis"] == "close"
    assert assumptions["commission_rate"] == "0.003"
    assert assumptions["sell_tax_rate"] == "0.003"
    assert assumptions["quantity"] == "2"


def test_a_saved_strategy_supplies_its_own_symbol_timeframe_and_code(auth_client, stub_market_data):
    strategy_id = _create_strategy(auth_client, WEEKLY_BUYER, name="weekly")

    resp = auth_client.post(
        "/api/backtests",
        json={
            "strategy_id": strategy_id,
            "start": _START.isoformat(),
            "end": (_START + timedelta(weeks=20)).isoformat(),
        },
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["symbol"] == "2330.TW"
    assert body["timeframe"] == "1wk"  # read from the strategy's own source
    assert body["strategy_id"] == strategy_id


def test_a_backtest_of_a_strategy_you_do_not_own_is_a_404(auth_client, stub_market_data):
    resp = auth_client.post(
        "/api/backtests",
        json={"strategy_id": 99999, "start": _START.isoformat(), "end": _END.isoformat()},
    )

    assert resp.status_code == 404


def test_source_the_sandbox_refuses_is_rejected_in_the_owners_language(
    auth_client, stub_market_data
):
    resp = auth_client.post(
        "/api/backtests", json=_request(source_code="import os\n" + DAILY_BUYER)
    )

    assert resp.status_code == 422
    assert "無法" in resp.json()["detail"] or "驗證" in resp.json()["detail"]


def test_either_a_strategy_or_source_is_required_but_not_both(auth_client, stub_market_data):
    both = auth_client.post("/api/backtests", json=_request(strategy_id=1))
    neither = auth_client.post(
        "/api/backtests",
        json={"start": _START.isoformat(), "end": _END.isoformat(), "symbol": "2330.TW"},
    )

    assert both.status_code == 422
    assert neither.status_code == 422


def test_an_end_before_the_start_is_rejected(auth_client, stub_market_data):
    resp = auth_client.post(
        "/api/backtests", json=_request(end=(_START - timedelta(days=1)).isoformat())
    )

    assert resp.status_code == 422


def test_a_symbol_with_no_history_reports_it_instead_of_pretending(auth_client, stub_market_data):
    resp = auth_client.post("/api/backtests", json=_request(symbol="NOPE.XX"))

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["result"]["summary"]["bars_tested"] == 0
    assert body["result"]["notes"]


# --- the guard --------------------------------------------------------------


def test_a_long_range_on_a_one_minute_candle_is_refused_before_anything_is_fetched(
    auth_client, stub_market_data
):
    """A year of 1-minute candles is half a million bars. On a free-tier box
    sharing its process with the live market loop, that has to be a refusal
    with a reason, not a request that quietly takes the worker down with it."""
    resp = auth_client.post(
        "/api/backtests",
        json=_request(
            source_code=TICK_BUYER,
            timeframe="1m",
            end=(_START + timedelta(days=365)).isoformat(),
        ),
    )

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert str(MAX_BACKTEST_BARS) in detail
    assert "區間" in detail


def test_a_range_just_inside_the_bound_is_allowed(auth_client, stub_market_data):
    resp = auth_client.post(
        "/api/backtests",
        json=_request(
            source_code=TICK_BUYER,
            timeframe="1m",
            end=(_START + timedelta(minutes=MAX_BACKTEST_BARS)).isoformat(),
        ),
    )

    assert resp.status_code == 201, resp.text


def test_an_on_bar_strategys_candle_size_may_not_be_overridden_by_the_request(
    auth_client, stub_market_data
):
    """`self.timeframe` is what the live loop fetches for this strategy, so a
    backtest on a different candle would be scoring code the owner cannot run.
    Refused with a reason rather than silently obeying either side."""
    resp = auth_client.post("/api/backtests", json=_request(timeframe="1wk"))

    assert resp.status_code == 422
    assert "self.timeframe" in resp.json()["detail"]


# --- revisiting a run -------------------------------------------------------


def test_a_run_is_persisted_and_can_be_read_back(auth_client, stub_market_data):
    created = auth_client.post("/api/backtests", json=_request()).json()

    listed = auth_client.get("/api/backtests")
    assert listed.status_code == 200
    rows = listed.json()
    assert [row["id"] for row in rows] == [created["id"]]
    # The list is for scanning, so it carries the summary but not the curve.
    assert rows[0]["summary"]["trade_count"] == 1
    assert "result" not in rows[0]

    detail = auth_client.get(f"/api/backtests/{created['id']}")
    assert detail.status_code == 200
    assert detail.json()["result"] == created["result"]


def test_a_run_keeps_the_code_it_scored_after_the_strategy_is_edited(auth_client, stub_market_data):
    """Otherwise last week's numbers silently start describing this week's
    code, which is the most convincing way to be wrong."""
    strategy_id = _create_strategy(auth_client)
    run_id = auth_client.post(
        "/api/backtests",
        json={"strategy_id": strategy_id, "start": _START.isoformat(), "end": _END.isoformat()},
    ).json()["id"]

    auth_client.patch(
        f"/api/strategies/{strategy_id}",
        json={"source_code": DAILY_BUYER.replace("self.seen == 3", "self.seen == 4")},
    )

    detail = auth_client.get(f"/api/backtests/{run_id}").json()
    assert "self.seen == 3" in detail["source_code"]


def test_deleting_a_strategy_leaves_its_runs_readable_and_unattributed(
    auth_client, stub_market_data
):
    strategy_id = _create_strategy(auth_client)
    run_id = auth_client.post(
        "/api/backtests",
        json={"strategy_id": strategy_id, "start": _START.isoformat(), "end": _END.isoformat()},
    ).json()["id"]

    assert auth_client.delete(f"/api/strategies/{strategy_id}").status_code == 204

    detail = auth_client.get(f"/api/backtests/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["strategy_id"] is None
    # The snapshot is what keeps the run readable once its subject is gone.
    assert detail.json()["strategy_name"] == "daily_buyer"


def test_runs_can_be_filtered_to_one_strategy(auth_client, stub_market_data):
    strategy_id = _create_strategy(auth_client)
    auth_client.post("/api/backtests", json=_request())
    attributed = auth_client.post(
        "/api/backtests",
        json={"strategy_id": strategy_id, "start": _START.isoformat(), "end": _END.isoformat()},
    ).json()["id"]

    rows = auth_client.get("/api/backtests", params={"strategy_id": strategy_id}).json()

    assert [row["id"] for row in rows] == [attributed]


def test_only_the_owner_can_read_a_run(auth_client, db_session, stub_market_data):
    run_id = auth_client.post("/api/backtests", json=_request()).json()["id"]
    stolen = db_session.get(BacktestRun, run_id)
    stolen.user_id = stolen.user_id + 1
    db_session.commit()

    assert auth_client.get(f"/api/backtests/{run_id}").status_code == 404


def test_old_runs_are_pruned_so_the_history_cannot_grow_without_bound(
    auth_client, stub_market_data, monkeypatch
):
    monkeypatch.setattr("app.api.routers.backtests.MAX_RUNS_PER_USER", 3)

    ids = [auth_client.post("/api/backtests", json=_request()).json()["id"] for _ in range(5)]

    kept = [row["id"] for row in auth_client.get("/api/backtests").json()]
    assert kept == list(reversed(ids[-3:]))


def test_the_endpoints_require_auth(client):
    assert client.get("/api/backtests").status_code == 401
    assert client.post("/api/backtests", json=_request()).status_code == 401
