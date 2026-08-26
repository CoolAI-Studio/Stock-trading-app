"""How a strategy has actually done since it went live.

The backtest produces a full report; a strategy that has been running for a
month produced nothing. The orders page says 策略訊號 without saying which
strategy, so with two running there was no way to tell them apart, and no page
anywhere answered "has this thing made or lost money".

Computed from the strategy's own confirmed fills, on the same footing as the
per-strategy capital gate (services/signals.py::_strategy_committed_cost):
what this strategy bought and sold, not what positions it happens to own. A
position belongs to whoever opened it and stays theirs however much everyone
else trades into it, so ownership is the wrong basis for a scorecard.

Average cost, because that is what the live ledger realises P&L against
(services/portfolio.py). A different basis here would have the two disagreeing
about the same trade.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy.orm import Session

from app.enums import OrderSide, OrderStatus
from app.models.order import Order
from app.models.strategy import Strategy


@dataclass
class StrategyPerformance:
    total_orders: int = 0
    filled_orders: int = 0
    # None, not zero, when nothing has filled: "0 元" reads as "it traded and
    # broke even", which is a different and much more informative statement
    # than "it has not traded".
    realized_pnl: Decimal | None = None
    open_quantity: Decimal = Decimal(0)
    open_cost: Decimal = Decimal(0)
    bought_value: Decimal = Decimal(0)
    sold_value: Decimal = Decimal(0)
    notes: list[str] = field(default_factory=list)


@dataclass
class _Book:
    """One symbol's running position for this strategy alone."""

    quantity: Decimal = Decimal(0)
    cost: Decimal = Decimal(0)

    @property
    def average(self) -> Decimal:
        return self.cost / self.quantity if self.quantity > 0 else Decimal(0)


def summarise(db: Session, strategy: Strategy) -> StrategyPerformance:
    orders = (
        db.query(Order)
        .filter(Order.user_id == strategy.user_id, Order.strategy_id == strategy.id)
        .order_by(Order.id)
        .all()
    )

    report = StrategyPerformance(total_orders=len(orders))
    books: dict[str, _Book] = {}
    realized = Decimal(0)

    for order in orders:
        if order.status != OrderStatus.CONFIRMED or order.fill_price is None:
            # Refused, expired and pending rows are counted in total_orders and
            # nowhere else. A strategy whose signals keep being refused looks
            # idle otherwise, and that gap is the thing worth noticing.
            continue

        quantity = order.filled_quantity if order.filled_quantity is not None else order.quantity
        if quantity is None or quantity <= 0:
            continue

        report.filled_orders += 1
        # Per symbol: netting across them would let a profit on one hide a loss
        # on the other, and produce a nonsense average cost.
        book = books.setdefault(order.symbol, _Book())
        value = quantity * order.fill_price

        if order.side == OrderSide.BUY:
            book.quantity += quantity
            book.cost += value
            report.bought_value += value
            continue

        report.sold_value += value
        # Only what this strategy actually holds can be realised. Selling into
        # somebody else's position is not this strategy's profit to claim.
        sold = min(quantity, book.quantity)
        if sold > 0:
            realized += (order.fill_price - book.average) * sold
            book.cost -= book.average * sold
            book.quantity -= sold

    if report.filled_orders:
        report.realized_pnl = realized
    report.open_quantity = sum((b.quantity for b in books.values()), Decimal(0))
    report.open_cost = sum((b.cost for b in books.values()), Decimal(0))
    report.notes = _notes(report)
    return report


def _notes(report: StrategyPerformance) -> list[str]:
    """What this number does and does not include.

    Said every time rather than left to a docs page: a figure read without its
    basis eventually gets compared against the backtest's, which uses a
    different one.
    """
    notes = [
        "這是毛損益，沒有扣手續費與證交稅——實際成交目前不收費用，"
        "回測會收，所以兩邊的數字本來就不同基準。",
        "只算這支策略自己買進、自己賣出的部分。它買進別人開的部位不會算成它的獲利。",
    ]
    if report.open_quantity > 0:
        notes.append("還有部位沒賣掉，那部分的賺賠不在上面的數字裡（那是未實現損益）。")
    if report.total_orders and not report.filled_orders:
        notes.append("這支策略發過訂單但一筆都沒成交——可能是都被拒絕、過期，或還在等你確認。")
    return notes
