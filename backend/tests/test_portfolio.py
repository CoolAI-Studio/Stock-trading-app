from decimal import Decimal

from app.models.enums import OrderSide, OrderSource, OrderStatus
from app.models.order import Order
from app.models.user import User
from app.services import portfolio


def _make_user(db_session) -> User:
    user = User(email="portfolio@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _make_order(db_session, user, side, quantity) -> Order:
    order = Order(
        user_id=user.id,
        source=OrderSource.MANUAL,
        symbol="AAPL",
        side=side,
        quantity=Decimal(quantity),
        status=OrderStatus.PENDING,
    )
    db_session.add(order)
    db_session.commit()
    db_session.refresh(order)
    return order


def test_first_buy_opens_a_position(db_session):
    user = _make_user(db_session)
    order = _make_order(db_session, user, OrderSide.BUY, 10)

    position = portfolio.apply_fill(db_session, order, Decimal(100), Decimal(10))

    assert position.quantity == Decimal(10)
    assert position.avg_entry_price == Decimal(100)
    assert position.opened_at is not None


def test_second_buy_averages_entry_price(db_session):
    user = _make_user(db_session)
    first = _make_order(db_session, user, OrderSide.BUY, 10)
    portfolio.apply_fill(db_session, first, Decimal(100), Decimal(10))

    second = _make_order(db_session, user, OrderSide.BUY, 10)
    position = portfolio.apply_fill(db_session, second, Decimal(120), Decimal(10))

    assert position.quantity == Decimal(20)
    assert position.avg_entry_price == Decimal(110)  # (100*10 + 120*10) / 20


def test_partial_sell_realizes_pnl_and_reduces_quantity(db_session):
    user = _make_user(db_session)
    buy = _make_order(db_session, user, OrderSide.BUY, 10)
    portfolio.apply_fill(db_session, buy, Decimal(100), Decimal(10))

    sell = _make_order(db_session, user, OrderSide.SELL, 4)
    position = portfolio.apply_fill(db_session, sell, Decimal(150), Decimal(4))

    assert position.quantity == Decimal(6)
    assert position.avg_entry_price == Decimal(100)  # unchanged by a sell
    assert position.realized_pnl == Decimal(200)  # (150-100)*4


def test_full_sell_flattens_position(db_session):
    user = _make_user(db_session)
    buy = _make_order(db_session, user, OrderSide.BUY, 10)
    portfolio.apply_fill(db_session, buy, Decimal(100), Decimal(10))

    sell = _make_order(db_session, user, OrderSide.SELL, 10)
    position = portfolio.apply_fill(db_session, sell, Decimal(90), Decimal(10))

    assert position.quantity == Decimal(0)
    assert position.avg_entry_price == Decimal(0)
    assert position.realized_pnl == Decimal(-100)  # (90-100)*10
