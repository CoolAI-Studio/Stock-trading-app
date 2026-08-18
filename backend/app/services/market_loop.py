import asyncio
import logging
from datetime import timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import SessionLocal
from app.models.enums import DataSource, OrderSide, OrderSource, OrderStatus
from app.models.mixins import utcnow
from app.models.order import Order
from app.models.position import Position
from app.models.risk import RiskSettings
from app.models.strategy import Strategy
from app.models.user import User
from app.services import alerts, risk, worker_health
from app.services.events import Event, bus
from app.services.market_data.base import Quote
from app.services.market_data.service import MarketDataService, get_market_data_service
from app.services.signals import SignalIn, create_pending_order
from app.services.strategy_runtime import StrategyRegistry

logger = logging.getLogger("app.market_loop")

# Module-level so strategy instances (and their accumulated self.prices
# state) survive across ticks, not just across a single tick_once() call.
_registry = StrategyRegistry()

_MAX_CONSECUTIVE_ERRORS = 5

# NOTE: v1 does not skip closed-market equities -- the per-provider TTL cache
# in MarketDataService already bounds request volume, and accurate
# multi-exchange market-hours detection (US/TW/holidays/timezones) is a
# fast-follow, not required for the core signal pipeline this phase verifies.


def _run_strategy(db: Session, strategy: Strategy, quote: Quote, events: list[Event]) -> None:
    try:
        loaded = _registry.get_or_load(strategy.id, strategy.source_code)
        signal_str = loaded.on_tick(float(quote.price))
    except Exception as exc:
        strategy.consecutive_errors += 1
        strategy.last_error = str(exc)
        strategy.last_run_at = utcnow()
        if strategy.consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
            strategy.is_active = False
            error_data = {
                "strategy_id": strategy.id,
                "error": str(exc),
                "user_id": strategy.user_id,
            }
            events.append(Event(type="strategy.error", data=error_data))
        db.commit()
        return

    strategy.last_run_at = utcnow()
    strategy.consecutive_errors = 0

    if signal_str in ("BUY", "SELL"):
        side = OrderSide.BUY if signal_str == "BUY" else OrderSide.SELL
        if strategy.alert_only:
            _emit_alert_only_signal(db, strategy, side, quote, signal_str, events)
            db.commit()
            return

        user = db.get(User, strategy.user_id)
        result = create_pending_order(
            db,
            user,
            SignalIn(
                symbol=strategy.symbol,
                side=side,
                source=OrderSource.STRATEGY,
                quantity=strategy.default_quantity,
                signal_price=quote.price,
                strategy_id=strategy.id,
            ),
        )
        if result.created:
            strategy.last_signal = signal_str
            strategy.last_signal_at = utcnow()
            order_data = {"order_id": result.order.id, "user_id": strategy.user_id}
            events.append(Event(type="order.created", data=order_data))

    db.commit()


def _emit_alert_only_signal(
    db: Session,
    strategy: Strategy,
    side: OrderSide,
    quote: Quote,
    signal_str: str,
    events: list[Event],
) -> None:
    """Watch-only path: no create_pending_order call at all, so none of the
    order-side gates (dedupe, cooldown, position/notional limits) apply --
    nothing here can move money."""
    event = alerts.emit_alert(db, strategy, side, quote.price)
    if event is None:
        return
    # Only stamped when the alert actually went out, matching the order path
    # where a gated signal leaves last_signal alone.
    strategy.last_signal = signal_str
    strategy.last_signal_at = utcnow()
    events.append(event)


def _check_position_exit(
    db: Session, position: Position, quote: Quote, events: list[Event]
) -> None:
    if position.avg_entry_price <= 0:
        return

    risk_settings = db.query(RiskSettings).filter(RiskSettings.user_id == position.user_id).first()
    if risk_settings is None:
        return

    hit_stop = risk.check_stop_loss(
        position.avg_entry_price, quote.price, risk_settings.stop_loss_pct
    )
    hit_target = risk.check_take_profit(
        position.avg_entry_price, quote.price, risk_settings.take_profit_pct
    )
    if not (hit_stop or hit_target):
        return

    user = db.get(User, position.user_id)
    result = create_pending_order(
        db,
        user,
        SignalIn(
            symbol=position.symbol,
            side=OrderSide.SELL,
            source=OrderSource.STRATEGY,
            quantity=position.quantity,
            signal_price=quote.price,
            risk_notes={"trigger": "stop_loss" if hit_stop else "take_profit"},
        ),
    )
    if result.created:
        order_data = {"order_id": result.order.id, "user_id": position.user_id}
        events.append(Event(type="order.created", data=order_data))


def _expire_stale_orders(db: Session, events: list[Event]) -> None:
    cutoff = utcnow() - timedelta(minutes=settings.PENDING_ORDER_EXPIRY_MINUTES)
    stale_orders = (
        db.query(Order).filter(Order.status == OrderStatus.PENDING, Order.created_at < cutoff).all()
    )
    for order in stale_orders:
        order.status = OrderStatus.EXPIRED
        order.decided_at = utcnow()
        data = {"order_id": order.id, "status": "expired", "user_id": order.user_id}
        events.append(Event(type="order.updated", data=data))
    if stale_orders:
        db.commit()


def tick_once(
    db: Session | None = None, market_data_service: MarketDataService | None = None
) -> list[Event]:
    """One full poll cycle. Fully synchronous so it can run inside
    asyncio.to_thread() from run_forever() without fighting a request-scoped
    session. Pass `db` explicitly in tests to inspect state on the same
    session afterward; production calls (no `db`) open and close their own.
    """
    service = market_data_service or get_market_data_service()
    owns_session = db is None
    session = db or SessionLocal()
    events: list[Event] = []

    try:
        strategies = session.query(Strategy).filter(Strategy.is_active.is_(True)).all()
        positions = session.query(Position).filter(Position.quantity > 0).all()

        symbols_by_source: dict[DataSource, set[str]] = {}
        for strat in strategies:
            symbols_by_source.setdefault(strat.data_source, set()).add(strat.symbol)
        for pos in positions:
            # Positions don't record their own data_source; default to
            # yfinance. A dedicated column is a fast-follow if this proves
            # wrong for a crypto-heavy portfolio.
            symbols_by_source.setdefault(DataSource.YFINANCE, set()).add(pos.symbol)

        quotes: dict[str, Quote] = {}
        for data_source, symbols in symbols_by_source.items():
            fetched = service.get_quotes(sorted(symbols), data_source)
            service.upsert_quotes(session, fetched)
            quotes.update(fetched)
            if fetched:
                events.append(Event(type="quote.update", data={"symbols": sorted(fetched)}))

        for strategy in strategies:
            quote = quotes.get(strategy.symbol)
            if quote is not None:
                _run_strategy(session, strategy, quote, events)

        for position in positions:
            quote = quotes.get(position.symbol)
            if quote is not None:
                _check_position_exit(session, position, quote, events)

        _expire_stale_orders(session, events)
    finally:
        if owns_session:
            session.close()

    for event in events:
        bus.publish(event)
    return events


async def run_forever(stop_event: asyncio.Event) -> None:
    logger.warning(
        "Starting background market-data worker in this process. Run with "
        "--workers 1 -- multiple worker processes would each run their own "
        "loop and duplicate signals for the same tick."
    )
    while not stop_event.is_set():
        # Marked before the tick, so a tick that hangs (and therefore never
        # returns to mark anything) shows up in /healthz as a stalled loop
        # rather than as a loop that is merely between polls.
        worker_health.heartbeat.mark_loop()
        try:
            await asyncio.to_thread(tick_once)
        except Exception:
            logger.exception("market loop tick failed")
        else:
            worker_health.heartbeat.mark_poll_success()
        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=settings.MARKET_DATA_POLL_INTERVAL_SEC
            )
        except TimeoutError:
            pass
    logger.info("market loop stopped")
