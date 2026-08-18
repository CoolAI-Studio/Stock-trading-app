from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.enums import OrderSide, OrderSource, OrderStatus
from app.models.mixins import utcnow
from app.models.order import Order
from app.models.position import Position
from app.models.strategy import Strategy
from app.models.user import User
from app.services import risk, risk_resolver
from app.services.events import Event, bus


@dataclass
class SignalIn:
    symbol: str
    side: OrderSide
    source: OrderSource
    quantity: Decimal
    signal_price: Decimal | None = None
    strategy_id: int | None = None
    risk_notes: dict | None = None
    idempotency_key: str | None = None
    raw_payload: dict | None = None


@dataclass
class SignalResult:
    order: Order | None
    created: bool
    reason: str | None = None


def _committed_cost(db: Session, user_id: int, strategy_id: int | None = None) -> Decimal:
    """Cost basis currently tied up in open positions -- the whole book, or
    just the slice one strategy opened. Summed in Python rather than SQL
    because the row count is tiny and Numeric arithmetic stays exact."""
    query = db.query(Position).filter(Position.user_id == user_id, Position.quantity > 0)
    if strategy_id is not None:
        query = query.filter(Position.strategy_id == strategy_id)
    return sum(
        (position.quantity * position.avg_entry_price for position in query.all()), Decimal(0)
    )


def create_pending_order(db: Session, user: User, signal: SignalIn) -> SignalResult:
    """The *only* path that creates an Order. Used identically by the worker
    loop, the TradingView webhook, and a manual POST -- every one of them
    goes through the same dedupe/cooldown/risk gate."""

    if signal.idempotency_key:
        existing = (
            db.query(Order)
            .filter(Order.user_id == user.id, Order.idempotency_key == signal.idempotency_key)
            .first()
        )
        if existing is not None:
            return SignalResult(order=existing, created=False, reason="duplicate idempotency_key")

    strategy = None
    if signal.strategy_id is not None:
        strategy = (
            db.query(Strategy)
            .filter(Strategy.id == signal.strategy_id, Strategy.user_id == user.id)
            .first()
        )
    global_settings = risk_resolver.get_or_create_global(db, user.id)
    limits = risk_resolver.resolve(global_settings, strategy)

    pending_same_side = (
        db.query(Order)
        .filter(
            Order.user_id == user.id,
            Order.symbol == signal.symbol,
            Order.side == signal.side,
            Order.status == OrderStatus.PENDING,
        )
        .first()
    )
    if pending_same_side is not None:
        return SignalResult(
            order=None, created=False, reason="a pending order for this symbol/side already exists"
        )

    if signal.strategy_id is not None and limits.signal_cooldown_sec > 0:
        cutoff = utcnow() - timedelta(seconds=limits.signal_cooldown_sec)
        recent = (
            db.query(Order)
            .filter(
                Order.user_id == user.id,
                Order.strategy_id == signal.strategy_id,
                Order.side == signal.side,
                Order.created_at >= cutoff,
            )
            .first()
        )
        if recent is not None:
            return SignalResult(order=None, created=False, reason="signal cooldown active")

    pending_count = (
        db.query(Order)
        .filter(
            Order.user_id == user.id,
            Order.symbol == signal.symbol,
            Order.status == OrderStatus.PENDING,
        )
        .count()
    )
    if pending_count >= limits.max_pending_orders_per_symbol:
        return SignalResult(
            order=None, created=False, reason="max pending orders for this symbol reached"
        )

    if signal.side == OrderSide.BUY:
        position = (
            db.query(Position)
            .filter(Position.user_id == user.id, Position.symbol == signal.symbol)
            .first()
        )
        current_qty = position.quantity if position else Decimal(0)

        within_limit = risk.check_position_limit(
            current_qty, signal.quantity, limits.max_position_qty
        )
        if not within_limit:
            return SignalResult(order=None, created=False, reason="position limit exceeded")

        if signal.signal_price is not None and limits.max_order_notional > 0:
            notional = signal.quantity * signal.signal_price
            if notional > limits.max_order_notional:
                return SignalResult(
                    order=None, created=False, reason="order notional exceeds max_order_notional"
                )

        # Priced buys only: without a signal price there is no cost to weigh
        # against the allocation, same as the notional gate above. Two
        # allocations are checked rather than one -- the global cap covers the
        # whole book, so it reads the global row directly and is never
        # displaced by an override; the strategy's own cap covers only the
        # positions that strategy opened.
        if signal.signal_price is not None:
            incoming_cost = signal.quantity * signal.signal_price
            if not risk.check_capital_limit(
                _committed_cost(db, user.id), incoming_cost, global_settings.capital
            ):
                return SignalResult(
                    order=None,
                    created=False,
                    reason="買進後的總持倉成本會超過全域本金上限，請調高本金或先減碼",
                )
            if strategy is not None and not risk.check_capital_limit(
                _committed_cost(db, user.id, strategy.id), incoming_cost, limits.capital
            ):
                return SignalResult(
                    order=None,
                    created=False,
                    reason=(
                        f"買進後「{strategy.name}」的持倉成本會超過該策略的本金上限，"
                        "請調高此策略本金或先減碼"
                    ),
                )

    order = Order(
        user_id=user.id,
        strategy_id=signal.strategy_id,
        source=signal.source,
        symbol=signal.symbol,
        side=signal.side,
        quantity=signal.quantity,
        signal_price=signal.signal_price,
        status=OrderStatus.PENDING,
        risk_notes=signal.risk_notes,
        idempotency_key=signal.idempotency_key,
        raw_payload=signal.raw_payload,
    )
    db.add(order)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(Order)
            .filter(Order.user_id == user.id, Order.idempotency_key == signal.idempotency_key)
            .first()
        )
        return SignalResult(order=existing, created=False, reason="duplicate idempotency_key")

    db.refresh(order)
    bus.publish(Event(type="order.created", data={"order_id": order.id, "user_id": user.id}))
    return SignalResult(order=order, created=True)
