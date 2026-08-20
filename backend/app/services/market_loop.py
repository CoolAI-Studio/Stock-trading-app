import asyncio
import logging
import time
from datetime import timedelta
from decimal import Decimal

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
from app.services import (
    alerts,
    backup_schedule,
    market_calendar,
    risk,
    risk_resolver,
    worker_health,
)
from app.services.events import Event, bus
from app.services.market_data.base import Bar, Quote, Timeframe
from app.services.market_data.service import MarketDataService, get_market_data_service
from app.services.notification import retry as notification_retry
from app.services.signals import SignalIn, create_pending_order
from app.services.strategy_runtime import (
    LoadedStrategy,
    StrategyRegistry,
    effective_warmup,
)

logger = logging.getLogger("app.market_loop")

# Module-level so strategy instances (and their accumulated self.prices
# state) survive across ticks, not just across a single tick_once() call.
# How many poll intervals a tick may take before it is worth a log line. Three
# is past "a slow provider day" and into "something is not coming back".
_SLOW_TICK_FACTOR = 3

# How long to wait between polls when nothing being watched is trading.
#
# Deliberately a slower poll rather than none at all: a daily-bar strategy's
# candle only closes after the session, so stopping entirely would push its
# signal to the next opening bell -- telling the owner at exactly the moment
# they needed to have already decided. Five minutes still collects that,
# while taking one symbol from ~17,000 requests a day to a few hundred
# against a scraper that blocks IPs for precisely that behaviour.
CLOSED_POLL_INTERVAL_SEC = 300.0

# What the last tick was watching, so working out the next sleep costs no
# query. Empty until the first tick, which means the first sleep is the slow
# one -- harmless, and better than a query that can fail before the app has a
# database.
_last_watched: list[tuple[str, DataSource]] = []

_registry = StrategyRegistry()


def release_strategy(strategy_id: int) -> None:
    """Forget the running instance of a strategy.

    The registry caches a compiled instance per id and that is deliberate --
    an MA5 strategy only works because `self.prices` survives between ticks.
    The same cache is why a strategy paused for two weeks used to resume with
    a price series that jumped straight from the old prices to today's: the
    gap is invisible to the strategy, so the first crossing it reported after
    resuming was an artefact of the pause, and it was a real order.

    Exposed here rather than letting routers reach into `_registry`, so the
    worker stays the only owner of the running state."""
    _registry.invalidate(strategy_id)


_MAX_CONSECUTIVE_ERRORS = 5

# NOTE: v1 does not skip closed-market equities -- the per-provider TTL cache
# in MarketDataService already bounds request volume, and accurate
# multi-exchange market-hours detection (US/TW/holidays/timezones) is a
# fast-follow, not required for the core signal pipeline this phase verifies.


def _record_strategy_error(
    db: Session, strategy: Strategy, exc: Exception, events: list[Event]
) -> None:
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


def _run_tick_strategy(
    db: Session, strategy: Strategy, loaded: LoadedStrategy, quote: Quote, events: list[Event]
) -> None:
    try:
        signal_str = loaded.on_tick(float(quote.price))
    except Exception as exc:
        _record_strategy_error(db, strategy, exc, events)
        return

    strategy.last_run_at = utcnow()
    strategy.consecutive_errors = 0
    _apply_signal(db, strategy, signal_str, quote.price, events)
    db.commit()


def _run_bar_strategy(
    db: Session, strategy: Strategy, loaded: LoadedStrategy, bars: list[Bar], events: list[Event]
) -> None:
    """Feeds closed candles to on_bar: at most one call per candle, and never
    for a candle the strategy has already been shown."""
    strategy.last_run_at = utcnow()

    warmup = effective_warmup(loaded, strategy.warmup_bars)
    if len(bars) < warmup:
        # An indicator handed 3 of the 35 candles it needs still returns a
        # number, and that number is garbage. Say so where the owner can read
        # it, and leave consecutive_errors alone: waiting for history is not
        # a fault, and must never retire the strategy.
        strategy.last_error = (
            f"warming up: {len(bars)}/{warmup} closed {loaded.timeframe.value} candles "
            "available so far -- no signals until then"
        )
        db.commit()
        return

    try:
        if loaded.last_bar_ts is None:
            # Every candle in hand closed before this instance existed, so
            # replay fills its memory and its signals are thrown away: a BUY
            # from three weeks ago is an observation, not an instruction.
            loaded.warm_up(bars)
            loaded.last_bar_ts = bars[-1].timestamp
            signal_bar, signal_str = bars[-1], "HOLD"
        else:
            new_bars = [bar for bar in bars if bar.timestamp > loaded.last_bar_ts]
            if not new_bars:
                db.commit()  # mid-candle poll: nothing has closed since last time
                return
            # Catch-up after an outage: the older candles still have to reach
            # the strategy so its state is right, but only the newest one can
            # justify an order. Acting on the rest would place trades now for
            # reasons that expired hours ago.
            if len(new_bars) > 1:
                loaded.warm_up(new_bars[:-1])
            signal_bar = new_bars[-1]
            signal_str = loaded.on_bar(signal_bar)
            loaded.last_bar_ts = signal_bar.timestamp
    except Exception as exc:
        _record_strategy_error(db, strategy, exc, events)
        return

    strategy.consecutive_errors = 0
    # The warm-up notice above is written here, so it is taken back here too.
    strategy.last_error = None
    # The candle's own close, not the live quote: the signal is a statement
    # about that candle, and pricing it off a tick minutes later would record
    # a reason and a price that never met.
    _apply_signal(db, strategy, signal_str, Decimal(str(signal_bar.close)), events)
    db.commit()


def _apply_signal(
    db: Session, strategy: Strategy, signal_str: str, price: Decimal, events: list[Event]
) -> None:
    if signal_str not in ("BUY", "SELL"):
        return

    side = OrderSide.BUY if signal_str == "BUY" else OrderSide.SELL
    if strategy.alert_only:
        _emit_alert_only_signal(db, strategy, side, price, signal_str, events)
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
            signal_price=price,
            strategy_id=strategy.id,
        ),
    )
    # Nothing announces the order here: create_pending_order() publishes
    # order.created itself, for every caller (worker loop, webhook, manual
    # POST) alike. Announcing it again from the loop sent the owner two
    # identical Telegram/email/push messages and wrote two notification_logs
    # rows for one order -- while a manual order, the one path that skips the
    # loop, correctly sent one.
    if result.created:
        strategy.last_signal = signal_str
        strategy.last_signal_at = utcnow()
        strategy.last_blocked_reason = None
        strategy.last_blocked_at = None
    else:
        # The refusal reason is the only difference between "this strategy is
        # quiet today" and "this strategy has been shouting BUY every tick and
        # being refused every tick". Two of create_pending_order's three
        # callers already surface it -- the manual POST as a 422, the webhook
        # in its response body -- and the loop is the one that had nobody to
        # hand it to.
        strategy.last_blocked_reason = result.reason
        strategy.last_blocked_at = utcnow()


def _emit_alert_only_signal(
    db: Session,
    strategy: Strategy,
    side: OrderSide,
    price: Decimal,
    signal_str: str,
    events: list[Event],
) -> None:
    """Watch-only path: no create_pending_order call at all, so none of the
    order-side gates (dedupe, cooldown, position/notional limits) apply --
    nothing here can move money."""
    event = alerts.emit_alert(db, strategy, side, price)
    if event is None:
        return
    # Only stamped when the alert actually went out, matching the order path
    # where a gated signal leaves last_signal alone.
    strategy.last_signal = signal_str
    strategy.last_signal_at = utcnow()
    events.append(event)


def _check_position_exit(db: Session, position: Position, quote: Quote) -> None:
    if position.avg_entry_price <= 0:
        return

    # The quote outside session hours is just the last close, and comparing it
    # to the entry price filed a SELL at 3am that nobody could act on -- which
    # then expired and was filed again on the next poll, several times a
    # night. The price cannot move while the market is shut, so there is
    # nothing here that will not still be true at the opening bell.
    if not market_calendar.is_open(position.symbol):
        return

    risk_settings = db.query(RiskSettings).filter(RiskSettings.user_id == position.user_id).first()
    if risk_settings is None:
        return

    # Stop-loss and take-profit are position-level, so the thresholds come
    # from whichever strategy opened this position -- not from whatever
    # strategy happens to be running now. Unattributed (manual order,
    # TradingView webhook) resolves to the global settings.
    owner = db.get(Strategy, position.strategy_id) if position.strategy_id else None
    limits = risk_resolver.resolve(risk_settings, owner)

    hit_stop = risk.check_stop_loss(position.avg_entry_price, quote.price, limits.stop_loss_pct)
    hit_target = risk.check_take_profit(
        position.avg_entry_price, quote.price, limits.take_profit_pct
    )
    if not (hit_stop or hit_target):
        return

    user = db.get(User, position.user_id)
    # Return value deliberately unused: the exit order announces itself from
    # inside create_pending_order (see _apply_signal), and a refused exit --
    # one already pending for this symbol/side -- needs nothing recorded here.
    create_pending_order(
        db,
        user,
        SignalIn(
            symbol=position.symbol,
            side=OrderSide.SELL,
            source=OrderSource.STRATEGY,
            # The exit belongs to the strategy that opened the position. An
            # unattributed exit closes the position but never credits the
            # capital back, so the strategy's own stop-loss would lock it out
            # of the allocation it just freed.
            strategy_id=position.strategy_id,
            quantity=position.quantity,
            signal_price=quote.price,
            risk_notes={"trigger": "stop_loss" if hit_stop else "take_profit"},
        ),
    )


def _expire_stale_orders(db: Session, events: list[Event]) -> None:
    cutoff = utcnow() - timedelta(minutes=settings.PENDING_ORDER_EXPIRY_MINUTES)
    stale_orders = (
        db.query(Order).filter(Order.status == OrderStatus.PENDING, Order.created_at < cutoff).all()
    )
    expired = False
    for order in stale_orders:
        # A daily-bar strategy's signal arrives after the close by definition,
        # so its order was always expired before the owner woke up -- which
        # made daily strategies effectively unusable. The clock is held while
        # the market is shut: the owner cannot act on an order at 3am, so the
        # 180 minutes should not be running. Crypto has no closing bell and is
        # therefore never held.
        if not market_calendar.is_open(order.symbol):
            continue
        order.status = OrderStatus.EXPIRED
        order.decided_at = utcnow()
        data = {"order_id": order.id, "status": "expired", "user_id": order.user_id}
        events.append(Event(type="order.updated", data=data))
        expired = True
    if expired:
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

        # Compiled up front rather than at call time, because the entry point
        # a strategy turned out to use decides what data it needs fetched:
        # a price tick, or a candle series at its own timeframe.
        loaded_by_id: dict[int, LoadedStrategy] = {}
        for strategy in strategies:
            try:
                loaded_by_id[strategy.id] = _registry.get_or_load(strategy.id, strategy.source_code)
            except Exception as exc:
                _record_strategy_error(session, strategy, exc, events)

        symbols_by_source: dict[DataSource, set[str]] = {}
        for strat in strategies:
            # Quotes are fetched for candle strategies too, even though they
            # never read one: the dashboard's price panel is driven off
            # MarketQuote rows, and a symbol would otherwise go blank the
            # moment its strategy switched to on_bar.
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

        # Only meaningful when something was actually asked for: an account
        # with no strategies and no positions requests nothing, and that is
        # not an outage. Asking and getting nothing is, and it is the state
        # that used to look perfectly healthy -- the providers swallow every
        # exception, so the loop kept completing polls on schedule while not
        # one price came back.
        # Recorded here rather than re-queried later: these are the rows the
        # tick already loaded, and they are exactly what decides how long to
        # sleep before the next one.
        global _last_watched
        _last_watched = [(strat.symbol, strat.data_source) for strat in strategies] + [
            (pos.symbol, DataSource.YFINANCE) for pos in positions
        ]

        if symbols_by_source:
            if quotes:
                worker_health.heartbeat.mark_quotes_fetched()
            else:
                worker_health.heartbeat.mark_quotes_empty()

        # One fetch per distinct symbol+timeframe, shared by every strategy
        # asking for it -- the history cache bounds this further still.
        bars_by_key: dict[tuple[DataSource, str, Timeframe], list[Bar]] = {}
        for strategy in strategies:
            loaded = loaded_by_id.get(strategy.id)
            if loaded is None or loaded.entry_point != "on_bar":
                continue
            key = (strategy.data_source, strategy.symbol, loaded.timeframe)
            if key not in bars_by_key:
                bars_by_key[key] = service.get_bars(
                    strategy.symbol, loaded.timeframe, strategy.data_source
                )

        for strategy in strategies:
            loaded = loaded_by_id.get(strategy.id)
            if loaded is None:
                continue  # failed to compile; already recorded above
            if loaded.entry_point == "on_bar":
                key = (strategy.data_source, strategy.symbol, loaded.timeframe)
                _run_bar_strategy(session, strategy, loaded, bars_by_key.get(key, []), events)
                continue
            quote = quotes.get(strategy.symbol)
            if quote is None:
                continue
            # Outside session hours the quote is just the last close, and
            # feeding it to on_tick thousands of times overnight walks the
            # strategy's own moving averages away from anything real before
            # the next session opens. on_bar strategies are unaffected --
            # closed_bars() already withholds a candle until it has closed.
            if not market_calendar.is_open(strategy.symbol, data_source=strategy.data_source):
                continue
            _run_tick_strategy(session, strategy, loaded, quote, events)

        for position in positions:
            quote = quotes.get(position.symbol)
            if quote is not None:
                _check_position_exit(session, position, quote)

        _expire_stale_orders(session, events)

        # Last, and deliberately inside the same try: a notification the owner
        # never received is this product's critical failure, so the sweep runs
        # every poll rather than on a timer of its own. It is bounded (one
        # indexed query, at most a handful of sends) so it cannot push the
        # stop-loss checks above it off schedule.
        try:
            notification_retry.retry_pending(session)
            # Cheap: one indexed query, and it only does real work on the day a
            # backup comes due. Living in the same sweep as the retry means
            # there is one place where "the worker is alive" implies "the
            # background chores are happening".
            backup_schedule.run_due(session)
        except Exception:
            # Never let a re-send failure take the market loop down with it --
            # the loop stopping is a strictly worse outcome than one alert
            # arriving late.
            logger.exception("notification retry sweep failed")
    finally:
        if owns_session:
            session.close()

        # Published from the finally on purpose. These used to go out on a line
        # AFTER the try, so any exception in the tick skipped them and the whole
        # batch evaporated with one line on stderr.
        #
        # That was permanent, not merely late, for the one event that matters
        # most: _record_strategy_error switches a strategy off and COMMITS
        # that, then only appends 「策略已停用」 here. The next tick queries
        # is_active.is_(True), so the strategy is no longer in it -- the error
        # threshold is crossed exactly once in a strategy's life. The retry
        # sweep cannot recover it either, because it resends existing
        # NotificationLog rows and this alert never reached the dispatcher.
        # The strategy stayed off, every future alert from it went with it, and
        # nothing told the owner.
        for event in events:
            try:
                bus.publish(event)
            except Exception:
                # One bad subscriber must not stop the rest of the batch.
                logger.exception("publishing %s failed", event.type)

    return events


def next_poll_delay(db: Session | None = None) -> float:
    """Seconds to wait before the next poll.

    Reads the watch list rather than the clock alone, because "is the market
    open" has no answer without knowing which markets are being watched -- a
    crypto strategy never sleeps, and a portfolio spanning Taipei and New York
    is awake for most of the day.

    Normally answered from what the last tick already loaded, so deciding how
    long to sleep costs no query at all. Passing a session is for tests that
    want an answer before any tick has run.

    Never raises. Getting the sleep length wrong is a small problem; letting
    it kill the loop that files stop-losses and sends alerts is not, and the
    first version of this did exactly that -- it opened its own session every
    iteration, so on a machine with no database yet the worker died on its
    first pass.
    """
    try:
        watched = _watched_symbols(db) if db is not None else _last_watched
        if not watched:
            # Nothing to watch is not the same as everything being shut, but
            # it is equally not a reason to poll hard.
            return CLOSED_POLL_INTERVAL_SEC
        if market_calendar.any_open(watched):
            return settings.MARKET_DATA_POLL_INTERVAL_SEC
        return CLOSED_POLL_INTERVAL_SEC
    except Exception:
        logger.exception("could not work out the next poll delay; using the normal interval")
        return settings.MARKET_DATA_POLL_INTERVAL_SEC


def _watched_symbols(db: Session) -> list[tuple[str, DataSource]]:
    watched = [
        (strategy.symbol, strategy.data_source)
        for strategy in db.query(Strategy).filter(Strategy.is_active.is_(True)).all()
    ]
    watched += [
        (position.symbol, DataSource.YFINANCE)
        for position in db.query(Position).filter(Position.quantity > 0).all()
    ]
    return watched


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
        started = time.monotonic()
        try:
            await asyncio.to_thread(tick_once)
        except Exception:
            logger.exception("market loop tick failed")
        else:
            worker_health.heartbeat.mark_poll_success()
        finally:
            # A tick that overruns the poll interval several times over is on
            # its way to wedging, usually on a provider socket that will never
            # answer. /healthz already catches a fully stuck loop -- mark_loop
            # stops being called and the age goes stale -- but by then the log
            # says nothing about which poll it was or how long it had been
            # degrading. yfinance 1.6 exposes no request timeout worth
            # trusting, so this records the symptom rather than pretending to
            # cure it.
            elapsed = time.monotonic() - started
            if elapsed > settings.MARKET_DATA_POLL_INTERVAL_SEC * _SLOW_TICK_FACTOR:
                logger.warning(
                    "market loop tick took %.1fs (poll interval is %ss)",
                    elapsed,
                    settings.MARKET_DATA_POLL_INTERVAL_SEC,
                )
        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=await asyncio.to_thread(next_poll_delay)
            )
        except TimeoutError:
            pass
    logger.info("market loop stopped")
