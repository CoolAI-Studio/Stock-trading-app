from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.enums import OrderSide, OrderSource, OrderStatus
from app.models.mixins import utcnow
from app.models.order import Order
from app.models.position import Position
from app.models.risk import RiskSettings
from app.models.user import User
from app.services import risk
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


def _get_or_create_risk_settings(db: Session, user: User) -> RiskSettings:
    row = db.query(RiskSettings).filter(RiskSettings.user_id == user.id).first()
    if row is None:
        row = RiskSettings(user_id=user.id)
        db.add(row)
        db.flush()
    return row


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

    risk_settings = _get_or_create_risk_settings(db, user)

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

    if signal.strategy_id is not None and risk_settings.signal_cooldown_sec > 0:
        cutoff = utcnow() - timedelta(seconds=risk_settings.signal_cooldown_sec)
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
    if pending_count >= risk_settings.max_pending_orders_per_symbol:
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
            current_qty, signal.quantity, risk_settings.max_position_qty
        )
        if not within_limit:
            return SignalResult(order=None, created=False, reason="position limit exceeded")

        if signal.signal_price is not None and risk_settings.max_order_notional > 0:
            notional = signal.quantity * signal.signal_price
            if notional > risk_settings.max_order_notional:
                return SignalResult(
                    order=None, created=False, reason="order notional exceeds max_order_notional"
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
