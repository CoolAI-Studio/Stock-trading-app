from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.enums import OrderSide
from app.models.mixins import utcnow
from app.models.order import Order
from app.models.position import Position


def apply_fill(db: Session, order: Order, fill_price: Decimal, fill_quantity: Decimal) -> Position:
    """Updates (or creates) the position for order.symbol from a confirmed
    fill. BUY grows the position with a weighted-average entry price; SELL
    reduces it and realizes P&L against the current average entry price."""
    position = (
        db.query(Position)
        .filter(Position.user_id == order.user_id, Position.symbol == order.symbol)
        .first()
    )
    if position is None:
        position = Position(user_id=order.user_id, symbol=order.symbol)
        db.add(position)
        # Column defaults (quantity/avg_entry_price/realized_pnl = 0) are only
        # applied by SQLAlchemy at flush time, not at construction -- flush
        # now so the arithmetic below doesn't operate on None.
        db.flush()

    if order.side == OrderSide.BUY:
        total_cost = position.avg_entry_price * position.quantity + fill_price * fill_quantity
        new_qty = position.quantity + fill_quantity
        position.avg_entry_price = (total_cost / new_qty) if new_qty > 0 else Decimal(0)
        position.quantity = new_qty
        if position.opened_at is None:
            position.opened_at = utcnow()
    else:
        closed_qty = min(fill_quantity, position.quantity)
        position.realized_pnl += (fill_price - position.avg_entry_price) * closed_qty
        position.quantity -= closed_qty
        if position.quantity <= 0:
            position.quantity = Decimal(0)
            position.avg_entry_price = Decimal(0)

    db.commit()
    db.refresh(position)
    return position
