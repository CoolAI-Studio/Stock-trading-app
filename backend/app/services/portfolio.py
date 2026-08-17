from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.enums import OrderSide
from app.models.mixins import utcnow
from app.models.order import Order
from app.models.position import Position


class InsufficientPositionError(Exception):
    """A confirmed fill cannot be applied to the position as it currently
    stands -- selling more than is held, or a position left in an impossible
    (negative) state. Callers surface this as a 422 so the owner fixes the
    position first, rather than the ledger quietly absorbing the discrepancy.
    """


def get_position(db: Session, user_id: int, symbol: str) -> Position | None:
    return (
        db.query(Position)
        .filter(Position.user_id == user_id, Position.symbol == symbol)
        .first()
    )


def ensure_fill_applicable(db: Session, order: Order, fill_quantity: Decimal) -> None:
    """Read-only precondition check, so the caller can reject a fill *before*
    marking the order confirmed. apply_fill re-runs it as defence in depth --
    the worker and the API both reach it, and only one of them checks first.
    """
    position = get_position(db, order.user_id, order.symbol)
    held = position.quantity if position is not None else Decimal(0)

    if held < 0:
        raise InsufficientPositionError(
            f"Position for {order.symbol} is negative ({held}). Correct it on the "
            "Positions page before confirming further orders."
        )

    if order.side == OrderSide.SELL and fill_quantity > held:
        raise InsufficientPositionError(
            f"Cannot sell {fill_quantity} {order.symbol}: only {held} held. "
            "Adjust the position first if it is out of date."
        )


def apply_fill(db: Session, order: Order, fill_price: Decimal, fill_quantity: Decimal) -> Position:
    """Updates (or creates) the position for order.symbol from a confirmed
    fill. BUY grows the position with a weighted-average entry price; SELL
    reduces it and realizes P&L against the current average entry price.

    Raises InsufficientPositionError rather than clamping: silently absorbing
    an oversell used to leave the order row and the position disagreeing about
    how much changed hands, with realized P&L computed on the smaller number.
    """
    ensure_fill_applicable(db, order, fill_quantity)

    position = get_position(db, order.user_id, order.symbol)
    if position is None:
        position = Position(user_id=order.user_id, symbol=order.symbol)
        db.add(position)
        # Column defaults (quantity/avg_entry_price/realized_pnl = 0) are only
        # applied by SQLAlchemy at flush time, not at construction -- flush
        # now so the arithmetic below doesn't operate on None.
        db.flush()

    if order.side == OrderSide.BUY:
        total_cost = position.avg_entry_price * position.quantity + fill_price * fill_quantity
        position.quantity = position.quantity + fill_quantity
        # quantity is guaranteed positive here: ensure_fill_applicable rejects
        # a negative starting position, and a BUY only ever adds to it.
        position.avg_entry_price = total_cost / position.quantity
        if position.opened_at is None:
            position.opened_at = utcnow()
    else:
        position.realized_pnl += (fill_price - position.avg_entry_price) * fill_quantity
        position.quantity -= fill_quantity
        if position.quantity == 0:
            # Flat: drop the cost basis so a later re-open starts clean rather
            # than averaging against a stale price.
            position.avg_entry_price = Decimal(0)

    db.commit()
    db.refresh(position)
    return position
