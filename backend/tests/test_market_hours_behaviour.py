"""What the worker does once it knows whether the market is open.

Three behaviours, all of which used to go wrong overnight and none of which
left a trace the owner could find in the morning.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.enums import DataSource, OrderSide, OrderSource, OrderStatus
from app.models.mixins import utcnow
from app.models.order import Order
from app.models.position import Position
from app.models.risk import RiskSettings
from app.models.strategy import Strategy
from app.models.user import User
from app.services import market_loop
from app.services.market_data.providers.mock_provider import MockProvider
from app.services.market_data.service import MarketDataService

# Every test here drives the clock itself; the autouse fixture in conftest
# that pretends the market is always open would defeat all of them.
pytestmark = pytest.mark.real_market_hours

# 2026-08-19 is a Wednesday.
TW_OPEN = datetime(2026, 8, 19, 2, 0, tzinfo=UTC)  # 10:00 Taipei
TW_SHUT = datetime(2026, 8, 19, 18, 0, tzinfo=UTC)  # 02:00 Taipei, next day

# 2026-08-22 是星期六，07:00 UTC = 03:00 紐約：美股關得最死的時刻。
#
# **加密貨幣的測試不能用 TW_SHUT。** 它是紐約星期三 14:00，美股正開著——所以一條用它
# 來測「加密貨幣沒有收盤鐘」的測試，測到的其實是「美股盤中」，不管代號被判成什麼都會
# 綠。底下那條 crypto 測試原本就是這樣，它宣稱守著的東西一次都沒守住過。
CRYPTO_WEEKEND = datetime(2026, 8, 22, 7, 0, tzinfo=UTC)

ALWAYS_BUY = """
class Strategy:
    def __init__(self):
        self.name = "always_buy"
        self.symbol = "2330.TW"
        self.ticks = 0

    def on_tick(self, current_price: float) -> str:
        self.ticks += 1
        return "BUY"
"""


def _user(db_session) -> User:
    user = User(email="hours@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    RiskSettings(user_id=user.id)
    db_session.add(RiskSettings(user_id=user.id, signal_cooldown_sec=0))
    db_session.commit()
    return user


def _service(price: float = 100.0) -> MarketDataService:
    return MarketDataService(providers={DataSource.YFINANCE: MockProvider({"2330.TW": price})})


def _at(moment: datetime):
    """Freeze what the calendar thinks 'now' is, without touching utcnow()."""
    return patch("app.services.market_calendar.datetime", _FrozenDatetime(moment))


class _FrozenDatetime:
    def __init__(self, moment: datetime) -> None:
        self._moment = moment

    def now(self, tz=None):
        return self._moment if tz is None else self._moment.astimezone(tz)

    def __getattr__(self, name):
        return getattr(datetime, name)


def test_no_stop_loss_order_is_filed_while_the_market_is_shut(db_session):
    """The scan used to compare a stale close against the entry price at 3am
    and file a SELL nobody could act on -- then do it again after the 180
    minute expiry, several times a night."""
    user = _user(db_session)
    _make_position(db_session, user)

    with _at(TW_SHUT):
        market_loop.tick_once(db=db_session, market_data_service=_service(price=50.0))

    assert db_session.query(Order).filter(Order.side == OrderSide.SELL).count() == 0


def test_the_same_stop_loss_does_fire_once_the_market_opens(db_session):
    user = _user(db_session)
    _make_position(db_session, user)

    with _at(TW_OPEN):
        market_loop.tick_once(db=db_session, market_data_service=_service(price=50.0))

    assert db_session.query(Order).filter(Order.side == OrderSide.SELL).count() == 1


def test_a_tick_strategy_is_not_fed_the_same_close_all_night(db_session):
    """Thousands of repeats of one price walk a moving average away from
    anything real before the next session even opens."""
    user = _user(db_session)
    strategy = Strategy(
        user_id=user.id,
        name="tw",
        symbol="2330.TW",
        source_code=ALWAYS_BUY,
        code_hash="h",
        is_active=True,
    )
    db_session.add(strategy)
    db_session.commit()

    with _at(TW_SHUT):
        market_loop.tick_once(db=db_session, market_data_service=_service())

    assert db_session.query(Order).filter(Order.side == OrderSide.BUY).count() == 0

    with _at(TW_OPEN):
        market_loop.tick_once(db=db_session, market_data_service=_service())

    assert db_session.query(Order).filter(Order.side == OrderSide.BUY).count() == 1


def test_a_pending_order_does_not_age_out_while_the_market_is_shut(db_session):
    """A daily-bar strategy's signal arrives after the close by definition.
    The order it created was always expired before the owner woke up, so daily
    strategies were effectively unusable."""
    user = _user(db_session)
    order = Order(
        user_id=user.id,
        source=OrderSource.STRATEGY,
        symbol="2330.TW",
        side=OrderSide.BUY,
        quantity=Decimal(1000),
        status=OrderStatus.PENDING,
    )
    db_session.add(order)
    db_session.commit()
    order.created_at = utcnow() - timedelta(hours=6)
    db_session.commit()

    with _at(TW_SHUT):
        market_loop.tick_once(db=db_session, market_data_service=_service())

    db_session.refresh(order)
    assert order.status == OrderStatus.PENDING, "it is 2am; the owner has not had a chance"


def test_a_stale_pending_order_still_expires_during_trading_hours(db_session):
    user = _user(db_session)
    order = Order(
        user_id=user.id,
        source=OrderSource.STRATEGY,
        symbol="2330.TW",
        side=OrderSide.BUY,
        quantity=Decimal(1000),
        status=OrderStatus.PENDING,
    )
    db_session.add(order)
    db_session.commit()
    order.created_at = utcnow() - timedelta(hours=6)
    db_session.commit()

    with _at(TW_OPEN):
        market_loop.tick_once(db=db_session, market_data_service=_service())

    db_session.refresh(order)
    assert order.status == OrderStatus.EXPIRED


def test_a_crypto_order_still_expires_at_three_in_the_morning(db_session):
    """Crypto has no closing bell, so nothing here should hold its clock.

    前提從 TW_SHUT 換成 CRYPTO_WEEKEND：前者是紐約星期三 14:00，美股正開著，所以這條
    測試無論 BTCUSDT 被判成什麼都會綠。斷言一個字沒改——改的是它終於真的在問那件事。
    """
    user = _user(db_session)
    order = Order(
        user_id=user.id,
        source=OrderSource.STRATEGY,
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity=Decimal(1),
        status=OrderStatus.PENDING,
    )
    db_session.add(order)
    db_session.commit()
    order.created_at = utcnow() - timedelta(hours=6)
    db_session.commit()

    with _at(CRYPTO_WEEKEND):
        market_loop.tick_once(db=db_session, market_data_service=_service())

    db_session.refresh(order)
    assert order.status == OrderStatus.EXPIRED


def _make_position(db_session, user) -> Position:
    position = Position(
        user_id=user.id,
        symbol="2330.TW",
        quantity=Decimal(1000),
        avg_entry_price=Decimal(100),
    )
    db_session.add(position)
    db_session.commit()
    db_session.refresh(position)
    return position


# --- how often to look when nothing is trading ------------------------------


def test_the_poll_slows_right_down_when_every_watched_market_is_shut(db_session):
    """One symbol at a 5s interval is ~17,000 requests a day, most of them at
    a closed market, against a scraper that blocks IPs for exactly that."""
    user = _user(db_session)
    db_session.add(
        Strategy(
            user_id=user.id,
            name="tw",
            symbol="2330.TW",
            source_code=ALWAYS_BUY,
            code_hash="h",
            is_active=True,
        )
    )
    db_session.commit()

    with _at(TW_SHUT):
        assert market_loop.next_poll_delay(db_session) == market_loop.CLOSED_POLL_INTERVAL_SEC


def test_the_poll_stays_fast_while_anything_is_trading(db_session):
    from app.config import settings

    user = _user(db_session)
    db_session.add(
        Strategy(
            user_id=user.id,
            name="tw",
            symbol="2330.TW",
            source_code=ALWAYS_BUY,
            code_hash="h",
            is_active=True,
        )
    )
    db_session.commit()

    with _at(TW_OPEN):
        assert market_loop.next_poll_delay(db_session) == settings.MARKET_DATA_POLL_INTERVAL_SEC


def test_bars_are_still_collected_overnight_so_a_daily_signal_is_not_lost(db_session):
    """Deliberately a slower poll rather than none at all. A daily-bar
    strategy's candle only closes after the session, so stopping entirely
    would push its signal to the next opening bell -- the owner would get
    told at the moment they needed to have already decided."""
    assert market_loop.CLOSED_POLL_INTERVAL_SEC > 0


def test_working_out_the_delay_never_kills_the_loop():
    """The first version of this opened its own database session every
    iteration to decide how long to sleep. On a machine with no database yet
    -- a fresh CI runner, a first boot -- the query raised inside the loop and
    the worker died on its first pass. Getting the sleep length wrong is a
    small problem; losing the loop that files stop-losses and sends alerts is
    not.
    """
    from app.config import settings

    with patch(
        "app.services.market_loop.market_calendar.any_open",
        side_effect=RuntimeError("no such table: strategies"),
    ):
        market_loop._last_watched = [("2330.TW", DataSource.YFINANCE)]
        assert market_loop.next_poll_delay() == settings.MARKET_DATA_POLL_INTERVAL_SEC


def test_the_delay_costs_no_query_once_a_tick_has_run(db_session):
    """Deciding how long to sleep should not be a database round trip every
    few seconds -- the tick already loaded exactly these rows."""
    user = _user(db_session)
    db_session.add(
        Strategy(
            user_id=user.id,
            name="tw",
            symbol="2330.TW",
            source_code=ALWAYS_BUY,
            code_hash="h",
            is_active=True,
        )
    )
    db_session.commit()

    with _at(TW_OPEN):
        market_loop.tick_once(db=db_session, market_data_service=_service())

    assert ("2330.TW", DataSource.YFINANCE) in market_loop._last_watched
