"""The market loop feeding candles to an on_bar strategy.

The rules being pinned here are the ones that make "on the close of the
second candle" mean something: one call per CLOSED candle, never twice for
the same one, never for a candle that closed before the strategy was
switched on, and never at all until the indicator has the history it needs.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.enums import DataSource
from app.models.order import Order
from app.models.strategy import Strategy
from app.models.user import User
from app.services import market_loop
from app.services.market_data.base import Bar, Quote, Timeframe
from app.services.market_data.service import MarketDataService
from app.services.strategy_runtime import StrategyRegistry

ALWAYS_BUY_ON_BAR = """
class Strategy:
    def __init__(self):
        self.name = "weekly_buyer"
        self.symbol = "2330.TW"
        self.timeframe = "1wk"
        self.warmup_bars = 3
        self.seen = []

    def on_bar(self, bar) -> str:
        self.seen.append(bar.close)
        return "BUY"
"""

ALWAYS_BUY_ON_TICK = """
class Strategy:
    def __init__(self):
        self.name = "ticker"
        self.symbol = "2330.TW"

    def on_tick(self, current_price: float) -> str:
        return "BUY"
"""

BROKEN_ON_BAR = """
class Strategy:
    def __init__(self):
        self.name = "boom"
        self.symbol = "2330.TW"
        self.timeframe = "1wk"
        self.warmup_bars = 1

    def on_bar(self, bar) -> str:
        raise RuntimeError("boom")
"""

_START = datetime(2026, 1, 5, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _fresh_registry(monkeypatch):
    """The registry is module-level so strategy state survives across polls.
    Across *tests* that would mean one test's warmed-up instance answering
    the next test's first poll."""
    monkeypatch.setattr(market_loop, "_registry", StrategyRegistry())


class _CandleProvider:
    """Serves a controllable weekly series plus the quotes the loop fetches
    for every active strategy regardless of its entry point."""

    data_source = DataSource.YFINANCE

    def __init__(self, closes: list[float]) -> None:
        self.closes = list(closes)
        self.bar_calls: list[tuple[str, Timeframe]] = []

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        return {
            s: Quote(symbol=s, data_source=self.data_source, price=Decimal(500)) for s in symbols
        }

    def get_bars(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Bar]:
        self.bar_calls.append((symbol, timeframe))
        return [
            Bar(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=_START + timedelta(weeks=i),
                open=close,
                high=close + 1,
                low=close - 1,
                close=close,
                volume=1000.0,
            )
            for i, close in enumerate(self.closes)
        ]


def _make_user(db_session, email="bars@example.com") -> User:
    user = User(email=email, hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _make_strategy(db_session, user, source_code, **overrides) -> Strategy:
    overrides.setdefault("is_active", True)
    strategy = Strategy(
        user_id=user.id,
        name="test-strategy",
        symbol="2330.TW",
        source_code=source_code,
        code_hash="irrelevant-for-tests",
        **overrides,
    )
    db_session.add(strategy)
    db_session.commit()
    db_session.refresh(strategy)
    return strategy


class _Harness:
    """A provider whose candle series the test can extend, plus a clock it can
    push past the history cache's TTL to make the next poll refetch."""

    def __init__(self, closes: list[float]) -> None:
        self.provider = _CandleProvider(closes)
        self.time = {"t": 0.0}
        self.service = MarketDataService(
            providers={DataSource.YFINANCE: self.provider},
            bar_ttl_sec=dict.fromkeys(Timeframe, 60.0),
            clock=lambda: self.time["t"],
        )

    def poll(self, db_session):
        return market_loop.tick_once(db=db_session, market_data_service=self.service)

    def next_week(self, close: float) -> None:
        self.provider.closes.append(close)
        self.time["t"] += 61.0

    def seen_by(self, strategy: Strategy) -> list[float]:
        return market_loop._registry.get_or_load(strategy.id, strategy.source_code).instance.seen


# --- warm-up ----------------------------------------------------------------


def test_a_bar_strategy_does_not_run_until_it_has_its_warmup_candles(db_session):
    """An RSI fed 2 of the 15 candles it needs still returns a number, and
    that number is garbage. Silence beats a confident wrong signal."""
    user = _make_user(db_session)
    strategy = _make_strategy(db_session, user, ALWAYS_BUY_ON_BAR)
    harness = _Harness([100.0, 101.0])  # declared warmup_bars is 3

    harness.poll(db_session)

    db_session.refresh(strategy)
    assert db_session.query(Order).count() == 0
    assert harness.seen_by(strategy) == []
    assert "2/3" in strategy.last_error


def test_waiting_for_warmup_is_not_an_error_that_deactivates_the_strategy(db_session):
    user = _make_user(db_session)
    strategy = _make_strategy(db_session, user, ALWAYS_BUY_ON_BAR)
    harness = _Harness([100.0])

    for _ in range(6):
        harness.time["t"] += 61.0
        harness.poll(db_session)

    db_session.refresh(strategy)
    assert strategy.consecutive_errors == 0
    assert strategy.is_active is True


def test_a_symbol_with_no_history_at_all_is_reported_not_crashed(db_session):
    user = _make_user(db_session)
    strategy = _make_strategy(db_session, user, ALWAYS_BUY_ON_BAR)
    harness = _Harness([])

    harness.poll(db_session)

    db_session.refresh(strategy)
    assert db_session.query(Order).count() == 0
    assert "0/3" in strategy.last_error


def test_it_falls_back_to_the_stored_warmup_when_the_code_declares_none(db_session):
    source = ALWAYS_BUY_ON_BAR.replace("        self.warmup_bars = 3\n", "")
    user = _make_user(db_session)
    strategy = _make_strategy(db_session, user, source, warmup_bars=4)
    harness = _Harness([100.0, 101.0, 102.0])

    harness.poll(db_session)

    db_session.refresh(strategy)
    assert "3/4" in strategy.last_error


# --- one call per closed candle ---------------------------------------------


def test_the_first_run_replays_history_without_trading(db_session):
    """Those candles closed before the owner switched this strategy on.
    Replaying them fills the indicator's memory; acting on them would place
    an order today for a reason that expired weeks ago."""
    user = _make_user(db_session)
    strategy = _make_strategy(db_session, user, ALWAYS_BUY_ON_BAR)
    harness = _Harness([100.0, 101.0, 102.0, 103.0])

    harness.poll(db_session)

    assert db_session.query(Order).count() == 0
    assert harness.seen_by(strategy) == [100.0, 101.0, 102.0, 103.0]
    db_session.refresh(strategy)
    assert strategy.last_error is None
    assert strategy.last_run_at is not None


def test_a_newly_closed_candle_fires_exactly_once(db_session):
    user = _make_user(db_session)
    strategy = _make_strategy(db_session, user, ALWAYS_BUY_ON_BAR)
    harness = _Harness([100.0, 101.0, 102.0])
    harness.poll(db_session)

    harness.next_week(110.0)
    harness.poll(db_session)

    orders = db_session.query(Order).all()
    assert len(orders) == 1
    assert orders[0].signal_price == Decimal(110)
    assert harness.seen_by(strategy) == [100.0, 101.0, 102.0, 110.0]


def test_polling_mid_candle_does_not_call_on_bar_again(db_session):
    user = _make_user(db_session)
    strategy = _make_strategy(db_session, user, ALWAYS_BUY_ON_BAR)
    harness = _Harness([100.0, 101.0, 102.0])
    harness.poll(db_session)
    harness.next_week(110.0)
    harness.poll(db_session)

    for _ in range(3):
        harness.time["t"] += 61.0
        harness.poll(db_session)

    assert harness.seen_by(strategy) == [100.0, 101.0, 102.0, 110.0]


def test_a_catch_up_of_several_candles_only_acts_on_the_newest(db_session):
    """The loop was down for a while. The older candles still have to reach
    the strategy so its state is right, but a signal from a candle that closed
    two weeks ago must not become an order at today's price."""
    user = _make_user(db_session)
    strategy = _make_strategy(db_session, user, ALWAYS_BUY_ON_BAR)
    harness = _Harness([100.0, 101.0, 102.0])
    harness.poll(db_session)

    harness.provider.closes.extend([103.0, 104.0, 105.0])
    harness.time["t"] += 61.0
    harness.poll(db_session)

    orders = db_session.query(Order).all()
    assert len(orders) == 1
    assert orders[0].signal_price == Decimal(105)
    assert harness.seen_by(strategy) == [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]


def test_the_declared_timeframe_is_the_one_fetched(db_session):
    user = _make_user(db_session)
    _make_strategy(db_session, user, ALWAYS_BUY_ON_BAR)
    harness = _Harness([100.0, 101.0, 102.0])

    harness.poll(db_session)

    assert harness.provider.bar_calls == [("2330.TW", Timeframe.WEEK_1)]


# --- on_tick strategies are untouched ---------------------------------------


def test_on_tick_strategies_never_ask_for_candles(db_session):
    user = _make_user(db_session)
    _make_strategy(db_session, user, ALWAYS_BUY_ON_TICK)
    harness = _Harness([100.0, 101.0, 102.0])

    harness.poll(db_session)

    assert harness.provider.bar_calls == []
    order = db_session.query(Order).one()
    assert order.signal_price == Decimal(500)  # the quote, exactly as before


# --- failures look like every other strategy failure ------------------------


def test_a_raising_on_bar_is_recorded_like_any_other_strategy_error(db_session):
    user = _make_user(db_session)
    strategy = _make_strategy(db_session, user, BROKEN_ON_BAR)
    harness = _Harness([100.0, 101.0])

    harness.poll(db_session)

    db_session.refresh(strategy)
    assert strategy.consecutive_errors == 1
    assert "boom" in strategy.last_error
    assert db_session.query(Order).count() == 0
