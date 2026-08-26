"""Alert-only strategies: notify and record, never create an order.

The clock that decides "have I already told the owner about this?" runs on
*delivered* alerts, not on attempts -- an alert that never left the box is
one the owner never saw, so it has to be retried rather than swallowed.
"""

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.enums import NotificationStatus, OrderSide
from app.models.mixins import utcnow
from app.models.strategy import Strategy, StrategyAlert
from app.services import risk_resolver
from app.services.events import Event
from app.services.notification import dispatcher

ALERT_EVENT_TYPE = "strategy.alert"

# How many consecutive failed deliveries are retried at full poll speed
# before an alert falls back to the normal alert_interval_sec.
#
# Three is enough to ride out what the retry exists for -- a Telegram blip or
# an SMTP timeout that clears within a few polls. Beyond that the channel is
# most likely broken for good (a revoked bot token never recovers on its own),
# and retrying every MARKET_DATA_POLL_INTERVAL_SEC would hammer a dead
# endpoint forever. The bound is a *fallback to the interval*, not a permanent
# stop: a dead channel then costs one attempt per alert_interval_sec instead
# of one per poll, so a channel the owner later fixes starts working again
# with no manual reset. The count is measured from the last delivered alert,
# so a single success clears it.
MAX_DELIVERY_ATTEMPTS = 3


def _is_throttled(db: Session, strategy_id: int, side: OrderSide, interval_sec: int) -> bool:
    """0 means notify every time -- including no retry bound, because at that
    setting a retry adds no attempt the owner has not already asked for."""
    if interval_sec <= 0:
        return False

    cutoff = utcnow() - timedelta(seconds=interval_sec)
    # Every comparison stays in SQL: created_at round-trips through SQLite as
    # a naive value, so comparing it to an aware utcnow() in Python would
    # raise.
    same_signal = db.query(StrategyAlert).filter(
        StrategyAlert.strategy_id == strategy_id, StrategyAlert.side == side
    )

    delivered_in_window = same_signal.filter(
        StrategyAlert.status == NotificationStatus.SENT, StrategyAlert.created_at >= cutoff
    ).first()
    if delivered_in_window is not None:
        return True

    last_delivered_id = (
        db.query(func.max(StrategyAlert.id))
        .filter(
            StrategyAlert.strategy_id == strategy_id,
            StrategyAlert.side == side,
            StrategyAlert.status == NotificationStatus.SENT,
        )
        .scalar()
        or 0
    )
    failures_since = same_signal.filter(
        StrategyAlert.status == NotificationStatus.FAILED, StrategyAlert.id > last_delivered_id
    )
    if failures_since.count() < MAX_DELIVERY_ATTEMPTS:
        return False

    return failures_since.filter(StrategyAlert.created_at >= cutoff).first() is not None


def emit_alert(db: Session, strategy: Strategy, side: OrderSide, price: Decimal) -> Event | None:
    """Notify the owner about an alert-only signal and record the attempt.

    Returns the event to publish, or None when the alert was throttled. The
    send happens here rather than through the bus because the outcome decides
    whether the throttle clock may start; the returned event is stamped so the
    bus-subscribed dispatcher does not send a second copy.
    """
    interval_sec = risk_resolver.resolve_for_user(db, strategy.user_id, strategy).alert_interval_sec
    if _is_throttled(db, strategy.id, side, interval_sec):
        return None

    event = Event(
        type=ALERT_EVENT_TYPE,
        data={
            "strategy_id": strategy.id,
            "strategy_name": strategy.name,
            "symbol": strategy.symbol,
            "side": side.value,
            "price": str(price),
            "user_id": strategy.user_id,
        },
    )
    result = dispatcher.handle_event(event, db=db)

    db.add(
        StrategyAlert(
            user_id=strategy.user_id,
            strategy_id=strategy.id,
            symbol=strategy.symbol,
            side=side,
            price=price,
            status=NotificationStatus.SENT if result.ok else NotificationStatus.FAILED,
            error=result.error or (None if result.ok else "no channel delivered this alert"),
        )
    )
    db.commit()

    event.data[dispatcher.DISPATCHED_INLINE_KEY] = True
    return event
