from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.db.session import get_db
from app.enums import OrderSource, OrderStatus
from app.models.mixins import utcnow
from app.models.order import Order
from app.models.user import User
from app.schemas.order import ManualOrderCreate, OrderConfirmRequest, OrderRead, OrderRejectRequest
from app.services import portfolio
from app.services.broker.manual import ManualConfirmBroker
from app.services.events import Event, bus
from app.services.signals import SignalIn, create_pending_order

router = APIRouter(prefix="/orders", tags=["orders"])

# v1's only broker adapter -- see app/services/broker/base.py for the
# interface a real broker integration would implement later.
_broker = ManualConfirmBroker()


def _publish_order_updated(order: Order) -> None:
    data = {"order_id": order.id, "status": order.status.value, "user_id": order.user_id}
    bus.publish(Event(type="order.updated", data=data))


def _get_owned_order(db: Session, user: User, order_id: int) -> Order:
    order = db.query(Order).filter(Order.id == order_id, Order.user_id == user.id).first()
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return order


@router.get("", response_model=list[OrderRead])
def list_orders(
    status_filter: OrderStatus | None = Query(default=None, alias="status"),
    symbol: str | None = None,
    source: OrderSource | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> list[Order]:
    query = db.query(Order).filter(Order.user_id == user.id)
    if status_filter is not None:
        query = query.filter(Order.status == status_filter)
    if symbol:
        query = query.filter(Order.symbol == symbol.upper())
    if source is not None:
        query = query.filter(Order.source == source)
    return query.order_by(Order.created_at.desc()).offset(offset).limit(limit).all()


@router.post("", response_model=OrderRead, status_code=status.HTTP_201_CREATED)
def create_manual_order(
    payload: ManualOrderCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> Order:
    result = create_pending_order(
        db,
        user,
        SignalIn(
            symbol=payload.symbol.upper(),
            side=payload.side,
            source=OrderSource.MANUAL,
            quantity=payload.quantity,
            signal_price=payload.signal_price,
        ),
    )
    if result.order is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=result.reason)
    return result.order


@router.get("/{order_id}", response_model=OrderRead)
def get_order(
    order_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> Order:
    return _get_owned_order(db, user, order_id)


@router.post("/{order_id}/confirm", response_model=OrderRead)
def confirm_order(
    order_id: int,
    payload: OrderConfirmRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> Order:
    order = _get_owned_order(db, user, order_id)
    if order.status != OrderStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"Order is already {order.status.value}"
        )

    fill_quantity = payload.quantity or order.quantity

    # Checked up-front, not after the fact: everything below this point mutates
    # and commits the order, so discovering the position can't take the fill
    # only once apply_fill runs would leave the order permanently marked
    # confirmed against a position that never moved.
    try:
        portfolio.ensure_fill_applicable(db, order, fill_quantity)
    except portfolio.InsufficientPositionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    result = _broker.submit(order, payload.fill_price, fill_quantity)
    if not result.ok:
        order.status = OrderStatus.FAILED
        order.reject_reason = result.error
        order.decided_at = utcnow()
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=result.error or "Broker rejected the order",
        )

    order.status = OrderStatus.CONFIRMED
    order.fill_price = result.fill_price
    # What reached the position, which is what `fill_quantity` is -- recording
    # `quantity` here would bill a partially filled order for the whole thing.
    order.filled_quantity = fill_quantity
    order.broker_ref = result.ref
    order.filled_at = utcnow()
    order.decided_at = utcnow()
    db.commit()

    portfolio.apply_fill(db, order, result.fill_price, fill_quantity)
    db.refresh(order)

    _publish_order_updated(order)
    return order


@router.post("/{order_id}/reject", response_model=OrderRead)
def reject_order(
    order_id: int,
    payload: OrderRejectRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> Order:
    order = _get_owned_order(db, user, order_id)
    if order.status != OrderStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"Order is already {order.status.value}"
        )

    order.status = OrderStatus.REJECTED
    order.reject_reason = payload.reason
    order.decided_at = utcnow()
    db.commit()
    db.refresh(order)

    _publish_order_updated(order)
    return order


# The two statuses that must never be deleted, and why. Kept as data rather
# than an if-chain so the message the owner reads lives next to the rule.
_UNDELETABLE = {
    OrderStatus.PENDING: (
        "這筆訂單還在等你決定，不能直接刪除。請按「拒絕」——拒絕會留下紀錄，"
        "刪掉則會讓這個訊號從此查不到。"
    ),
    OrderStatus.CONFIRMED: (
        "這筆訂單已經成交、動到了持倉，也算進策略的本金額度裡，刪掉會讓帳目對不起來。"
        "如果數量或成本記錯了，請到「部位」頁調整。"
    ),
}


@router.delete("/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_order(
    order_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> None:
    """Remove a decided, inert order from the history.

    Rejected, expired and failed rows are clutter -- they moved nothing and
    nothing reads them. Confirmed and pending rows are refused: a confirmed
    order moved a position and is counted by the per-strategy capital gate
    (services/signals.py::_strategy_committed_cost), so deleting it silently
    frees capital still being held and leaves the position disagreeing with
    the order history, neither of which is visible from this page.
    """
    order = _get_owned_order(db, user, order_id)
    refusal = _UNDELETABLE.get(order.status)
    if refusal:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=refusal)
    db.delete(order)
    db.commit()


@router.delete("")
def clear_order_history(
    db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> dict[str, int]:
    """Clear the inert history in one go, leaving confirmed and pending alone
    for the same reasons."""
    deleted = (
        db.query(Order)
        .filter(Order.user_id == user.id, Order.status.notin_(list(_UNDELETABLE)))
        .delete(synchronize_session=False)
    )
    db.commit()
    return {"deleted": deleted}
