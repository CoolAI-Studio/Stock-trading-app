from datetime import timedelta
from decimal import Decimal

from app.models.enums import DataSource, OrderSide, OrderStatus
from app.models.mixins import utcnow
from app.models.order import Order
from app.models.position import Position
from app.models.risk import RiskSettings
from app.models.strategy import Strategy
from app.models.user import User
from app.services import market_loop
from app.services.market_data.providers.mock_provider import MockProvider
from app.services.market_data.service import MarketDataService

ALWAYS_BUY_SOURCE = """
class Strategy:
    def __init__(self):
        self.name = "always_buy"
        self.symbol = "AAPL"

    def on_tick(self, current_price: float) -> str:
        return "BUY"
"""

BROKEN_SOURCE = """
class Strategy:
    def __init__(self):
        self.name = "boom"
        self.symbol = "AAPL"

    def on_tick(self, current_price: float) -> str:
        raise RuntimeError("boom")
"""


def _make_user(db_session, email="loop@example.com") -> User:
    user = User(email=email, hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _make_strategy(db_session, user, source_code, symbol="AAPL", **overrides) -> Strategy:
    overrides.setdefault("is_active", True)
    strategy = Strategy(
        user_id=user.id,
        name="test-strategy",
        symbol=symbol,
        source_code=source_code,
        code_hash="irrelevant-for-tests",
        **overrides,
    )
    db_session.add(strategy)
    db_session.commit()
    db_session.refresh(strategy)
    return strategy


def _mock_service(symbol="AAPL", price=100.0) -> MarketDataService:
    provider = MockProvider(base_prices={symbol: price})
    return MarketDataService(providers={DataSource.YFINANCE: provider})


def test_tick_creates_a_pending_order_for_an_active_strategy(db_session):
    user = _make_user(db_session)
    _make_strategy(db_session, user, ALWAYS_BUY_SOURCE)

    events = market_loop.tick_once(db=db_session, market_data_service=_mock_service())

    order = db_session.query(Order).filter(Order.symbol == "AAPL").first()
    assert order is not None
    assert order.status == OrderStatus.PENDING
    assert any(e.type == "order.created" for e in events)


def test_second_tick_does_not_duplicate_the_pending_order(db_session):
    user = _make_user(db_session)
    _make_strategy(db_session, user, ALWAYS_BUY_SOURCE)

    market_loop.tick_once(db=db_session, market_data_service=_mock_service())
    market_loop.tick_once(db=db_session, market_data_service=_mock_service())

    orders = db_session.query(Order).filter(Order.symbol == "AAPL").all()
    assert len(orders) == 1


def test_inactive_strategy_is_ignored(db_session):
    user = _make_user(db_session)
    _make_strategy(db_session, user, ALWAYS_BUY_SOURCE, is_active=False)

    market_loop.tick_once(db=db_session, market_data_service=_mock_service())

    assert db_session.query(Order).count() == 0


def test_loop_survives_a_raising_strategy(db_session):
    user = _make_user(db_session)
    strategy = _make_strategy(db_session, user, BROKEN_SOURCE)

    events = market_loop.tick_once(db=db_session, market_data_service=_mock_service())

    db_session.refresh(strategy)
    assert strategy.consecutive_errors == 1
    assert strategy.last_error
    assert db_session.query(Order).count() == 0
    assert isinstance(events, list)  # tick_once returned normally, did not raise


def test_strategy_auto_deactivates_after_five_consecutive_errors(db_session):
    user = _make_user(db_session)
    strategy = _make_strategy(db_session, user, BROKEN_SOURCE)

    for _ in range(5):
        market_loop.tick_once(db=db_session, market_data_service=_mock_service())

    db_session.refresh(strategy)
    assert strategy.consecutive_errors == 5
    assert strategy.is_active is False


def test_position_stop_loss_creates_a_sell_signal(db_session):
    user = _make_user(db_session)
    db_session.add(RiskSettings(user_id=user.id, stop_loss_pct=Decimal("0.1")))
    db_session.add(
        Position(user_id=user.id, symbol="AAPL", quantity=Decimal(10), avg_entry_price=Decimal(100))
    )
    db_session.commit()

    events = market_loop.tick_once(db=db_session, market_data_service=_mock_service(price=85.0))

    order = (
        db_session.query(Order)
        .filter(Order.symbol == "AAPL", Order.side == OrderSide.SELL)
        .first()
    )
    assert order is not None
    assert order.risk_notes["trigger"] == "stop_loss"
    assert any(e.type == "order.created" for e in events)


def test_expires_stale_pending_orders(db_session):
    user = _make_user(db_session)
    stale_order = Order(
        user_id=user.id,
        source="manual",
        symbol="TSLA",
        side=OrderSide.BUY,
        quantity=Decimal(1),
        status=OrderStatus.PENDING,
    )
    db_session.add(stale_order)
    db_session.commit()
    # backdate created_at well past the expiry window
    stale_order.created_at = utcnow() - timedelta(hours=999)
    db_session.commit()

    market_loop.tick_once(db=db_session, market_data_service=_mock_service())

    db_session.refresh(stale_order)
    assert stale_order.status == OrderStatus.EXPIRED
