from decimal import Decimal

from app.enums import OrderSide, OrderSource, OrderStatus
from app.models.risk import RiskSettings
from app.models.user import User
from app.services.signals import SignalIn, create_pending_order


def _make_user(db_session, email="signals@example.com") -> User:
    user = User(email=email, hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _signal(**overrides) -> SignalIn:
    base = dict(
        symbol="AAPL",
        side=OrderSide.BUY,
        source=OrderSource.STRATEGY,
        quantity=Decimal(1),
        signal_price=Decimal(100),
    )
    base.update(overrides)
    return SignalIn(**base)


def test_creates_a_pending_order(db_session):
    user = _make_user(db_session)

    result = create_pending_order(db_session, user, _signal())

    assert result.created is True
    assert result.order.status == OrderStatus.PENDING
    assert result.order.symbol == "AAPL"


def test_duplicate_pending_for_same_symbol_and_side_is_refused(db_session):
    user = _make_user(db_session)

    first = create_pending_order(db_session, user, _signal())
    second = create_pending_order(db_session, user, _signal())

    assert first.created is True
    assert second.created is False
    assert second.order is None


def test_opposite_side_is_not_deduped(db_session):
    user = _make_user(db_session)

    buy = create_pending_order(db_session, user, _signal(side=OrderSide.BUY))
    sell = create_pending_order(db_session, user, _signal(side=OrderSide.SELL))

    assert buy.created is True
    assert sell.created is True


def test_idempotency_key_prevents_duplicate_orders(db_session):
    user = _make_user(db_session)

    first = create_pending_order(db_session, user, _signal(idempotency_key="tv-alert-1"))
    second = create_pending_order(
        db_session, user, _signal(symbol="TSLA", idempotency_key="tv-alert-1")
    )

    assert first.created is True
    assert second.created is False
    assert second.order.id == first.order.id


def test_cooldown_blocks_repeat_signal_from_same_strategy(db_session):
    user = _make_user(db_session)
    risk_settings = RiskSettings(user_id=user.id, signal_cooldown_sec=300)
    db_session.add(risk_settings)
    db_session.commit()

    first = create_pending_order(db_session, user, _signal(strategy_id=1))
    # reject the pending order so dedupe-by-pending doesn't mask the cooldown check
    first.order.status = OrderStatus.REJECTED
    db_session.commit()

    second = create_pending_order(db_session, user, _signal(strategy_id=1, symbol="TSLA"))

    assert second.created is False
    assert "cooldown" in second.reason


def test_max_pending_orders_per_symbol_is_enforced(db_session):
    user = _make_user(db_session)
    risk_settings = RiskSettings(user_id=user.id, max_pending_orders_per_symbol=1)
    db_session.add(risk_settings)
    db_session.commit()

    first = create_pending_order(db_session, user, _signal(symbol="AAPL", side=OrderSide.BUY))
    first.order.status = OrderStatus.REJECTED
    db_session.commit()

    # a second BUY for AAPL should be allowed since the first is no longer pending...
    second = create_pending_order(db_session, user, _signal(symbol="AAPL", side=OrderSide.BUY))
    assert second.created is True

    # ...but a third (with two pending: the SELL doesn't count against BUY's
    # per-symbol cap in this test, so use a different side to hit the cap)
    third = create_pending_order(db_session, user, _signal(symbol="AAPL", side=OrderSide.SELL))
    assert third.created is False
    assert "max pending" in third.reason


def test_position_limit_blocks_oversized_buy(db_session):
    user = _make_user(db_session)
    risk_settings = RiskSettings(user_id=user.id, max_position_qty=Decimal(10))
    db_session.add(risk_settings)
    db_session.commit()

    result = create_pending_order(
        db_session, user, _signal(side=OrderSide.BUY, quantity=Decimal(15))
    )

    assert result.created is False
    assert "position limit" in result.reason


def test_sell_signals_are_not_gated_by_position_limit(db_session):
    user = _make_user(db_session)
    risk_settings = RiskSettings(user_id=user.id, max_position_qty=Decimal(1))
    db_session.add(risk_settings)
    db_session.commit()

    result = create_pending_order(
        db_session, user, _signal(side=OrderSide.SELL, quantity=Decimal(1000))
    )

    assert result.created is True


def test_max_pending_orders_per_symbol_zero_means_unlimited(db_session):
    # The third of the three traps: `pending >= 0` is already true before a
    # single order exists, so 0 used to block every order silently. It now
    # means "no limit", matching the other seven knobs.
    user = _make_user(db_session)
    db_session.add(RiskSettings(user_id=user.id, max_pending_orders_per_symbol=0))
    db_session.commit()

    buy = create_pending_order(db_session, user, _signal(side=OrderSide.BUY))
    sell = create_pending_order(db_session, user, _signal(side=OrderSide.SELL))

    assert buy.created is True
    assert sell.created is True
