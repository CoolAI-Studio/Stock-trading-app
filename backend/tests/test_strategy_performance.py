"""How a strategy has actually done since it went live.

The backtest gives a full report; a strategy that has been running for a month
gave nothing. The orders page says 策略訊號 without saying which strategy, so
with two running there was no way to tell them apart, and no page anywhere
answered "has this thing made or lost money".

Computed from the strategy's own confirmed fills, on the same footing as the
per-strategy capital gate (services/signals.py::_strategy_committed_cost) --
what it bought and sold, not what positions it happens to own.
"""

from decimal import Decimal

from app.models.enums import OrderSide, OrderSource, OrderStatus
from app.models.order import Order
from app.models.strategy import Strategy
from app.models.user import User
from app.services import strategy_performance


def _user(db_session) -> User:
    """auth_client creates one; a db_session-only test does not, so make sure
    there is one either way."""
    existing = db_session.query(User).first()
    if existing is not None:
        return existing
    user = User(email="perf@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _strategy(db_session, user, name="perf") -> Strategy:
    strategy = Strategy(
        user_id=user.id,
        name=name,
        symbol="2330.TW",
        source_code="class Strategy: pass",
        code_hash="h",
    )
    db_session.add(strategy)
    db_session.commit()
    db_session.refresh(strategy)
    return strategy


def _fill(db_session, user, strategy, side, quantity, price, symbol="2330.TW"):
    order = Order(
        user_id=user.id,
        strategy_id=strategy.id if strategy else None,
        source=OrderSource.STRATEGY,
        symbol=symbol,
        side=side,
        quantity=Decimal(quantity),
        status=OrderStatus.CONFIRMED,
        fill_price=Decimal(price),
        filled_quantity=Decimal(quantity),
    )
    db_session.add(order)
    db_session.commit()
    return order


def test_a_strategy_that_has_never_traded_reports_nothing_rather_than_zero(db_session):
    """Zero profit reads as "it traded and broke even", which is a different
    and much more informative statement than "it has not traded"."""
    user = _user(db_session)
    strategy = _strategy(db_session, user)

    report = strategy_performance.summarise(db_session, strategy)
    assert report.filled_orders == 0
    assert report.realized_pnl is None


def test_a_completed_round_trip_reports_its_profit(db_session):
    user = _user(db_session)
    strategy = _strategy(db_session, user)
    _fill(db_session, user, strategy, OrderSide.BUY, 1000, 1000)
    _fill(db_session, user, strategy, OrderSide.SELL, 1000, 1100)

    report = strategy_performance.summarise(db_session, strategy)
    assert report.realized_pnl == Decimal(100000)
    assert report.filled_orders == 2


def test_a_losing_round_trip_reports_a_loss(db_session):
    user = _user(db_session)
    strategy = _strategy(db_session, user)
    _fill(db_session, user, strategy, OrderSide.BUY, 1000, 1000)
    _fill(db_session, user, strategy, OrderSide.SELL, 1000, 900)

    assert strategy_performance.summarise(db_session, strategy).realized_pnl == Decimal(-100000)


def test_selling_half_realises_half(db_session):
    """Average cost, which is what the live ledger uses -- a different basis
    here would make the two disagree about the same trade."""
    user = _user(db_session)
    strategy = _strategy(db_session, user)
    _fill(db_session, user, strategy, OrderSide.BUY, 1000, 1000)
    _fill(db_session, user, strategy, OrderSide.SELL, 500, 1200)

    report = strategy_performance.summarise(db_session, strategy)
    assert report.realized_pnl == Decimal(100000)
    assert report.open_quantity == Decimal(500)


def test_buying_at_two_prices_averages_the_cost(db_session):
    user = _user(db_session)
    strategy = _strategy(db_session, user)
    _fill(db_session, user, strategy, OrderSide.BUY, 1000, 1000)
    _fill(db_session, user, strategy, OrderSide.BUY, 1000, 1200)
    _fill(db_session, user, strategy, OrderSide.SELL, 1000, 1100)

    # Average cost is 1100, so selling at 1100 is flat.
    assert strategy_performance.summarise(db_session, strategy).realized_pnl == Decimal(0)


def test_another_strategys_fills_are_not_counted(db_session):
    user = _user(db_session)
    mine = _strategy(db_session, user, name="mine")
    theirs = _strategy(db_session, user, name="theirs")
    _fill(db_session, user, theirs, OrderSide.BUY, 1000, 1000)
    _fill(db_session, user, theirs, OrderSide.SELL, 1000, 2000)

    assert strategy_performance.summarise(db_session, mine).filled_orders == 0


def test_two_symbols_are_kept_apart(db_session):
    """Netting across symbols would let a profit on one hide a loss on the
    other, and produce a nonsense average cost."""
    user = _user(db_session)
    strategy = _strategy(db_session, user)
    _fill(db_session, user, strategy, OrderSide.BUY, 1000, 1000, symbol="2330.TW")
    _fill(db_session, user, strategy, OrderSide.BUY, 10, 200, symbol="AAPL")
    _fill(db_session, user, strategy, OrderSide.SELL, 10, 250, symbol="AAPL")

    report = strategy_performance.summarise(db_session, strategy)
    assert report.realized_pnl == Decimal(500)
    assert report.open_quantity == Decimal(1000)


def test_refused_and_expired_orders_are_counted_but_not_priced(db_session):
    """A strategy whose signals keep being refused looks idle otherwise, and
    that is the thing worth noticing."""
    user = _user(db_session)
    strategy = _strategy(db_session, user)
    for status_ in (OrderStatus.REJECTED, OrderStatus.EXPIRED, OrderStatus.PENDING):
        order = Order(
            user_id=user.id,
            strategy_id=strategy.id,
            source=OrderSource.STRATEGY,
            symbol="2330.TW",
            side=OrderSide.BUY,
            quantity=Decimal(1000),
            status=status_,
        )
        db_session.add(order)
    db_session.commit()

    report = strategy_performance.summarise(db_session, strategy)
    assert report.total_orders == 3
    assert report.filled_orders == 0
    assert report.realized_pnl is None


def test_the_report_reaches_the_api(auth_client, db_session):
    user = _user(db_session)
    strategy = _strategy(db_session, user)
    _fill(db_session, user, strategy, OrderSide.BUY, 1000, 1000)
    _fill(db_session, user, strategy, OrderSide.SELL, 1000, 1100)

    body = auth_client.get(f"/api/strategies/{strategy.id}/performance").json()
    assert Decimal(body["realized_pnl"]) == Decimal(100000)
    assert body["filled_orders"] == 2


def test_another_persons_strategy_is_a_404(auth_client, db_session):
    from app.core.security import hash_password

    other = User(email="nosy@example.com", hashed_password=hash_password("pw12345678"))
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)
    theirs = _strategy(db_session, other, name="theirs")

    assert auth_client.get(f"/api/strategies/{theirs.id}/performance").status_code == 404


def test_the_report_says_costs_are_not_included(auth_client, db_session):
    """Live fills do not charge commission or tax yet (a known gap), and the
    backtest does. A figure that silently used a different basis from the
    backtest's would be worse than no figure."""
    user = _user(db_session)
    strategy = _strategy(db_session, user)
    _fill(db_session, user, strategy, OrderSide.BUY, 1000, 1000)

    body = auth_client.get(f"/api/strategies/{strategy.id}/performance").json()
    assert body["notes"]
    assert any("手續費" in note for note in body["notes"])
