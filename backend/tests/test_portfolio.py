from decimal import Decimal

from app.enums import OrderSide, OrderSource, OrderStatus
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


def test_selling_more_than_held_is_rejected_instead_of_silently_truncated(db_session):
    """Regression: `min(fill_quantity, position.quantity)` used to discard the
    excess without a word. Holding 10 and confirming a sell of 25 left the
    order row saying 25, the position saying 10 were closed, and realized P&L
    computed on 10 -- a ledger that no longer reconciles with itself, with
    nothing surfaced to the owner. Worse, the position was then flat, so the
    symbol dropped out of stop-loss monitoring entirely.
    """
    user = _make_user(db_session)
    buy = _make_order(db_session, user, OrderSide.BUY, 10)
    portfolio.apply_fill(db_session, buy, Decimal(100), Decimal(10))

    sell = _make_order(db_session, user, OrderSide.SELL, 25)
    try:
        portfolio.apply_fill(db_session, sell, Decimal(150), Decimal(25))
    except portfolio.InsufficientPositionError as exc:
        assert "25" in str(exc) and "10" in str(exc)
    else:
        raise AssertionError("overselling was accepted")

    # The position must be left exactly as it was -- no partial application.
    position = portfolio.get_position(db_session, user.id, "AAPL")
    assert position.quantity == Decimal(10)
    assert position.avg_entry_price == Decimal(100)
    assert position.realized_pnl == Decimal(0)


def test_selling_the_exact_held_quantity_still_works(db_session):
    """The boundary the rejection must not overshoot."""
    user = _make_user(db_session)
    buy = _make_order(db_session, user, OrderSide.BUY, 10)
    portfolio.apply_fill(db_session, buy, Decimal(100), Decimal(10))

    sell = _make_order(db_session, user, OrderSide.SELL, 10)
    position = portfolio.apply_fill(db_session, sell, Decimal(150), Decimal(10))

    assert position.quantity == Decimal(0)
    assert position.avg_entry_price == Decimal(0)
    assert position.realized_pnl == Decimal(500)


def test_selling_against_no_position_is_rejected(db_session):
    user = _make_user(db_session)
    sell = _make_order(db_session, user, OrderSide.SELL, 1)

    try:
        portfolio.apply_fill(db_session, sell, Decimal(150), Decimal(1))
    except portfolio.InsufficientPositionError:
        pass
    else:
        raise AssertionError("selling with no position was accepted")


def test_a_negative_position_quantity_is_rejected_rather_than_zeroing_cost_basis(db_session):
    """Regression: a negative quantity (reachable through the manual position
    adjust form, which accepted `-5`) made the next BUY's `new_qty > 0` guard
    fail, silently resetting avg_entry_price to 0. Every later sell then
    computed P&L against a fabricated cost basis, and stop-loss compared
    against it too.
    """
    user = _make_user(db_session)
    buy = _make_order(db_session, user, OrderSide.BUY, 10)
    portfolio.apply_fill(db_session, buy, Decimal(100), Decimal(10))

    position = portfolio.get_position(db_session, user.id, "AAPL")
    position.quantity = Decimal(-5)  # what the unvalidated PATCH used to allow
    db_session.commit()

    follow_up = _make_order(db_session, user, OrderSide.BUY, 3)
    try:
        portfolio.apply_fill(db_session, follow_up, Decimal(160), Decimal(3))
    except portfolio.InsufficientPositionError:
        pass
    else:
        position = portfolio.get_position(db_session, user.id, "AAPL")
        raise AssertionError(
            f"buy on a negative position was accepted; avg_entry_price is now "
            f"{position.avg_entry_price}"
        )
