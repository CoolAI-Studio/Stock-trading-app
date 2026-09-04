import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import SessionLocal
from app.enums import DataSource, OrderSide, OrderSource, OrderStatus
from app.models.market import MarketQuote
from app.models.mixins import utcnow
from app.models.order import Order
from app.models.position import Position
from app.models.risk import RiskSettings
from app.models.strategy import Strategy
from app.models.user import User
from app.services import (
    alerts,
    backup_schedule,
    build_info,
    market_calendar,
    risk,
    risk_resolver,
    strategy_pool,
    strategy_worker,
    worker_health,
)
from app.services.events import Event, bus
from app.services.market_data.base import Bar, Quote, Timeframe
from app.services.market_data.service import MarketDataService, get_market_data_service
from app.services.notification import retry as notification_retry
from app.services.signals import SignalIn, create_pending_order
from app.services.strategy_pool import PooledStrategy, StrategyPool
from app.services.strategy_runtime import (
    LoadedStrategy,
    StrategyRegistry,
    effective_warmup,
)
from app.services.strategy_worker import WorkerUnavailable

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
# they needed to have already decided. Half an hour still collects that,
# while taking one symbol from ~17,000 requests a day to a few dozen
# against a scraper that blocks IPs for precisely that behaviour.
#
# ＊ 為什麼是半小時而不是五分鐘。
#
# 這個數字直接決定使用者的資料庫活不活得過一個月。Neon 免費方案給 100 CU-hours
# （官方換算 0.25 CU 跑 400 小時），而它**閒置五分鐘才休眠、免費方案關不掉**——五分
# 鐘的輪詢剛好踩在那個門檻上，於是資料庫幾乎不休眠，一個月要 730 小時，大約第 17 天
# 額度用完，然後停到下一個帳單週期。停著的那半個月，一則提醒都不會送出。
#
# 半小時讓它有 25 分鐘是睡著的，整個月用得掉的降到 400 小時以內。換來的代價是收盤後
# 的日線訊號最多晚半小時——而那段時間市場是關的，他本來就要等到隔天才動得了。
#
# **改小之前先讀 CLAUDE.md 那一節。** 這一格看起來像一個效能參數，實際上是免費方案的
# 額度預算，而它壞掉的樣子是「每個月後半完全沒有提醒」。
#
# 只看加密貨幣的人不適用：24 小時開盤 = 永遠走快的那條路 = 資料庫永遠不睡。那種情況
# 只能換一家不用運算時數計費的（例如 Supabase）。
CLOSED_POLL_INTERVAL_SEC = 1800.0

# 開機時回頭看，多長的空白才算「這段時間沒有人在盯盤」。
#
# 二十分鐘，而不是十五。十五是 Render 免費方案休眠的門檻，但**每一次更新也是一段空
# 白**——建置加部署在免費方案上要好幾分鐘，而更新是我們自己造成的、每一次推送都會發
# 生。把它算進去的話，這句話會在第一天就變成雜訊，然後真的睡了八小時那一次長得一模
# 一樣。
#
# 寧可漏掉一次短的休眠：短的漏掉的提醒少，而一個被學會忽略的訊號漏掉的是全部。
DOWNTIME_THRESHOLD_SEC = 20 * 60.0

# What the last tick was watching, so working out the next sleep costs no
# query. Empty until the first tick, which means the first sleep is the slow
# one -- harmless, and better than a query that can fail before the app has a
# database.
_last_watched: list[tuple[str, DataSource]] = []

# 使用者的策略跑在這裡面的子行程，不在這個行程（#18 第 3 步）。
#
# 名字保持 _registry，而 StrategyPool 的介面刻意跟 StrategyRegistry 一模一樣，所以
# 這次搬家在 tick_once 裡的 diff 幾乎是零——看得出來哪幾行才真的改變了行為。
_registry: StrategyPool | StrategyRegistry = StrategyPool()


def reset_strategy_workers() -> None:
    """關掉所有子行程，下一次呼叫再重建。

    測試用它把每一條隔開；正式環境不需要呼叫——1900 個測試如果各自留下幾個子行
    程，就是這台機器上那個「跑到一半 Failed to start threads worker」的來源。
    """
    global _registry
    if isinstance(_registry, StrategyPool):
        _registry.shutdown()
    _registry = StrategyPool()


def shutdown_strategy_workers() -> None:
    """關掉所有策略子行程。app 收攤的時候呼叫。"""
    if isinstance(_registry, StrategyPool):
        _registry.shutdown()
    # 驗證用的那個一次性 worker 也要關。它不在池裡，所以池的 shutdown 碰不到它。
    strategy_pool.shutdown_scratch()


def stuck_children_still_running() -> list[int]:
    """逾時之後還活著的子行程 PID。正常永遠是空的。

    這是 #18 相對於舊做法真正買到的東西，而它必須**驗得到**：
    strategy_runtime._guarded 的檔頭誠實地寫著 Python 殺不掉執行緒，逾時的策略會
    一直燒著一顆核心直到行程重啟。子行程殺得掉——這個函式就是問「真的殺掉了嗎」。
    """
    return strategy_worker.abandoned_children_still_running()


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


# 盯盤迴圈只要求策略有這幾個東西，不在乎它跑在哪個行程裡。
RunnableStrategy = LoadedStrategy | PooledStrategy

_MAX_CONSECUTIVE_ERRORS = 5

# NOTE: v1 does not skip closed-market equities -- the per-provider TTL cache
# in MarketDataService already bounds request volume, and accurate
# multi-exchange market-hours detection (US/TW/holidays/timezones) is a
# fast-follow, not required for the core signal pipeline this phase verifies.


def _record_strategy_error(
    db: Session, strategy: Strategy, exc: Exception, events: list[Event], blocked: set[int]
) -> None:
    """`blocked` 跟 `events` 一樣是拿來往外帶東西的：這一輪有哪幾支策略是因為子行程
    叫不動而沒跑成。tick_once 收齊之後交給心跳，那是 /healthz 和狀態頁唯一看得到這件
    事的管道。"""
    if isinstance(exc, WorkerUnavailable):
        # 記下來，但**只是記下來**：底下那些「累積、停用」一個都不做。
        #
        # 這一行是「看得見」與「怪罪使用者」的分界。子行程起不來的時候，這個部署一則
        # 提醒都發不出去，而在這之前它在 /healthz 上是全綠的、狀態頁上一格都沒有、看
        # 門狗永遠不會寄信——唯一的線索是這一列上的一句話，而那一頁他不會打開。
        blocked.add(strategy.id)
        # **子行程壞掉不是策略的錯，所以不能累積、不能停用。**
        #
        # 這裡連續五次就把策略停用，而輪詢五秒一次。一次 spawn 失敗（記憶體不夠、
        # 容器剛重啟、唯讀的檔案系統）如果走這條路，二十五秒之後使用者每一支策略
        # 都會被永久停用——而且狀況恢復之後沒有任何東西會把它們打開，畫面上只會寫
        # 著「停用」。
        #
        # 跟 _record_feed_problem 同一個道理，只是換了一個來源：壞掉的程式碼不會自
        # 己好，所以那條路留給它；子行程起不來會自己好，所以只留一句話在那一列上。
        _record_feed_problem(db, strategy, f"策略行程暫時不可用：{exc}")
        return

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


_UPGRADE_MARK = "系統更新之後"


def _mark_compiled(db: Session, strategy: Strategy) -> None:
    """記下這支策略最後一次編譯成功時的版本，並收回更新造成的那句話。"""
    changed = False
    running = build_info.commit()
    if running and strategy.last_compiled_version != running:
        strategy.last_compiled_version = running
        changed = True
    # 那句話是這裡寫的，所以也在這裡收回——而且**只收回那一句**。
    #
    # 它裡面有「在修好之前它不會發出任何提醒」，留著就是一句主動誤導的話：策略明明
    # 已經在跑了。其他來源的 last_error 不碰，那些有它們自己的清除時機。
    if strategy.last_error and strategy.last_error.startswith(_UPGRADE_MARK):
        strategy.last_error = None
        changed = True
    if changed:
        db.commit()


def _record_compile_failure(
    db: Session, strategy: Strategy, exc: Exception, events: list[Event], blocked: set[int]
) -> None:
    """編不過。**是他的程式碼壞了，還是我們的更新弄壞的？**

    這個分辨是整條路的重點。走錯邊的後果不對稱：

      當成他的錯（而其實是我們的）→ 二十五秒後策略永久停用，沒有東西會打開它，
                                    畫面上只寫「停用」。**提醒全面停擺。**
      當成我們的錯（而其實是他的）→ 那一列一直留著一句錯誤訊息，他看得到。

    所以分界是「它在**上一個版本**編得過嗎」：編得過，就是我們動了什麼；沒編過或
    版本沒變，就照舊算它的錯——不然「連續五次就停用」這條保護整個消失，而它本來是
    有理由存在的。

    跟 #18 的 WorkerUnavailable 是同一條原則，只是來源換成我們自己的更新：基礎設施
    的問題不可以走「停用使用者的東西」那條路。
    """
    running = build_info.commit()
    was = strategy.last_compiled_version
    upgraded = bool(was) and bool(running) and was != running

    if not upgraded:
        _record_strategy_error(db, strategy, exc, events, blocked)
        return

    message = (
        f"{_UPGRADE_MARK}，這支策略編不過了（上一個編得過的版本是 {was}，"
        f"現在是 {running}）。**這不是你的程式碼的問題**，而且它還開著——"
        f"但在修好之前它不會發出任何提醒。原因：{exc}"
    )
    already_told = strategy.last_error == message
    strategy.last_run_at = utcnow()
    strategy.last_error = message
    db.commit()

    if not already_told:
        # 一支不會發訊號的策略等於提醒停擺，所以他必須知道。但輪詢五秒一次，每一輪
        # 都通知會讓他關掉通知——那比不通知更糟。所以只在訊息第一次出現時發。
        events.append(
            Event(
                type="strategy.error",
                data={"strategy_id": strategy.id, "error": message, "user_id": strategy.user_id},
            )
        )


def _record_feed_problem(db: Session, strategy: Strategy, detail: str) -> None:
    """行情抓不到。**這不是策略的錯，所以不會累積、不會關掉它。**

    _record_strategy_error 連續五次就把策略停用，而輪詢是五秒一次——照那條路走，
    上游擋你二十五秒，使用者的每一支策略就永久停用了，而且擋單結束之後沒有任何
    東西會把它們打開。畫面上只會寫著「停用」，沒有人看得出來為什麼。

    壞掉的程式碼不會自己好，所以那條路留給它；抓不到資料會自己好，所以這裡只留
    一句話在那一列上，等下一輪抓到就被 _run_bar_strategy 清掉。

    Same shape as the warm-up notice above it: something to read, nothing that
    retires the strategy.
    """
    strategy.last_run_at = utcnow()
    strategy.last_error = detail
    db.commit()


def _run_tick_strategy(
    db: Session,
    strategy: Strategy,
    loaded: RunnableStrategy,
    quote: Quote,
    events: list[Event],
    blocked: set[int],
) -> None:
    try:
        signal_str = loaded.on_tick(float(quote.price))
    except Exception as exc:
        _record_strategy_error(db, strategy, exc, events, blocked)
        return

    strategy.last_run_at = utcnow()
    strategy.consecutive_errors = 0
    _apply_signal(db, strategy, signal_str, quote.price, events)
    db.commit()


def _run_bar_strategy(
    db: Session,
    strategy: Strategy,
    loaded: RunnableStrategy,
    bars: list[Bar],
    events: list[Event],
    blocked: set[int],
    *,
    feed_failed: bool = False,
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
        progress = f"{len(bars)}/{warmup} closed {loaded.timeframe.value} candles"
        if feed_failed:
            # 「warming up: 2/3」是一句會自己過去的話：再收一根就好了。上游斷
            # 著的時候那一根不會來，所以同一句話變成謊——他會坐在那裡等一個永遠
            # 不會到的提醒，而畫面上一切正常。手上有幾根還是要講，不然他不知道
            # 恢復之後還要等多久。
            strategy.last_error = f"抓不到 K 棒：上游沒有回應。手上只有 {progress}"
        else:
            strategy.last_error = (
                f"warming up: {progress} available so far -- no signals until then"
            )
        db.commit()
        return

    try:
        # **讀一次，存起來。** 跑在子行程上的策略（PooledStrategy）把這個屬性當成
        # 問句：實例不在了就回 None，好讓這裡重新暖一次身。而那個答案會在任兩次讀
        # 取之間翻面（release_strategy 跑在請求執行緒上：使用者按了儲存／暫停／刪
        # 除；也可能是子行程被 OOM 殺掉）。把它留在下面那句 list comprehension 的條件裡
        # 就是每一根 K 棒重讀一次（線上一輪 300 根），中途翻面則 `bar.timestamp > None`
        # → TypeError。TypeError 不是 WorkerUnavailable，所以 _record_strategy_error 會把「我
        # 們的子行程沒了」寫成「你的程式壞了」，累積五次永久停用。
        last_bar_ts = loaded.last_bar_ts
        if last_bar_ts is None:
            # 這個實例沒有記憶，所以不論如何都要用**完整歷史**重暖一次。剩下的問題
            # 是：重暖完之後，最新那一根算不算「新的」。
            #
            # `fed_through` 回答的正是這件事，而它跟上面那個 property 的差別只有
            # 「實例還在嗎」。兩種處境要的行為不一樣：
            #
            #   從來沒餵過       手上那幾根可能是任意久以前收盤的（半夜三點建一支
            #                    日線策略，最新那根是昨天的收盤），所以訊號是觀察
            #                    不是指示——整批重播，丟掉。
            #
            #   餵過，但實例被殺 我們知道上一輪餵到哪一根，所以那根**剛剛收盤的**
            #                    完全有依據下判斷。丟掉它的話，對日線策略來說是一
            #                    整天的提醒消失，而且不會再回來（#58）。而子行程被
            #                    殺掉是這個設計裡正常會發生的事：同一格上任何一支
            #                    策略逾時就會發生。
            fed_through = loaded.fed_through
            if fed_through is None or bars[-1].timestamp <= fed_through:
                loaded.warm_up(bars)
                loaded.last_bar_ts = bars[-1].timestamp
                signal_bar, signal_str = bars[-1], "HOLD"
            else:
                # 重暖到最新那一根**之前**，再對它下判斷——不能整批餵完再叫一次
                # on_bar，那會讓同一根 K 棒進去兩次，而滾動視窗會把它算兩遍。
                #
                # 只有最新那一根可以下指示，跟底下追進度那條路同一條規則：更早那
                # 幾根的理由已經過期了。
                if len(bars) > 1:
                    loaded.warm_up(bars[:-1])
                signal_bar = bars[-1]
                signal_str = loaded.on_bar(signal_bar)
                loaded.last_bar_ts = signal_bar.timestamp
        else:
            new_bars = [bar for bar in bars if bar.timestamp > last_bar_ts]
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
        _record_strategy_error(db, strategy, exc, events, blocked)
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
    # 這一輪有哪幾支策略因為子行程叫不動而沒跑成。
    #
    # 往下傳給每一個記錄錯誤的地方，收齊之後交給心跳——那是 /healthz、狀態頁和外部看
    # 門狗唯一看得到這件事的管道。在這之前，「三個 worker 都起不來」等於提醒全面停擺，
    # 而每一項檢查都是綠的。
    blocked: set[int] = set()

    try:
        strategies = session.query(Strategy).filter(Strategy.is_active.is_(True)).all()
        positions = session.query(Position).filter(Position.quantity > 0).all()

        # Compiled up front rather than at call time, because the entry point
        # a strategy turned out to use decides what data it needs fetched:
        # a price tick, or a candle series at its own timeframe.
        loaded_by_id: dict[int, RunnableStrategy] = {}
        for strategy in strategies:
            try:
                # The owner's tuned parameters, not just the source. Loading
                # without them would store a setting the form displays and the
                # running strategy ignores.
                loaded_by_id[strategy.id] = _registry.get_or_load(
                    strategy.id, strategy.source_code, params=strategy.params
                )
            except Exception as exc:
                _record_compile_failure(session, strategy, exc, events, blocked)
            else:
                # 編得過就記下版本。這是下一次判斷「是不是我們的更新造成的」唯一的
                # 依據，而它必須在**成功**的時候寫，不是在失敗的時候猜。
                _mark_compiled(session, strategy)

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
                # EMPTY, deliberately. This event has no user_id, so
                # ws/broadcast.py sends it to every open connection -- and
                # `fetched` is the union of every account's strategy and
                # position symbols. The old comment said 「a symbol quote is
                # the same for everyone watching it」, which is true of the
                # PRICE and false of 「who is watching what」: a holdings list
                # is one of the most personal things in this app, and it was
                # going out every five seconds.
                #
                # Nothing is lost: the frontend only ever reacted by
                # invalidating its own quote query (lib/useWebSocket.ts) and
                # refetching with its own credentials. It never read the
                # payload.
                events.append(Event(type="quote.update", data={}))

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

        # 整組重寫：沒被列出來的就是這一輪沒有這個問題（跑成功了，或根本沒輪到
        # 它——關市時 on_tick 策略不會被呼叫）。「沒有被問」不等於「壞掉」。
        worker_health.heartbeat.mark_blocked_strategies(blocked)

        if symbols_by_source:
            if quotes:
                worker_health.heartbeat.mark_quotes_fetched()
            else:
                worker_health.heartbeat.mark_quotes_empty()

        # Per symbol, not just per poll. `if quotes` above is satisfied by ONE
        # price, so nine working symbols hid the tenth that never resolved.
        # Called even when nothing was asked for, because that is how a symbol
        # the owner has stopped watching gets forgotten -- deleting the bad
        # row has to actually clear the alarm.
        worker_health.heartbeat.mark_symbols(
            {symbol for symbols in symbols_by_source.values() for symbol in symbols},
            set(quotes),
        )

        # One fetch per distinct symbol+timeframe, shared by every strategy
        # asking for it -- the history cache bounds this further still.
        bars_by_key: dict[tuple[DataSource, str, Timeframe], list[Bar]] = {}
        # PER KEY, because an unguarded fetch here used to end the whole round.
        # tick_once's own try has a finally and no except, so anything raised
        # walked out to run_forever -- skipping every remaining strategy, the
        # stop-loss scan, the order expiry AND the pending-notification sweep.
        # One symbol nobody could price cost every alert in that round.
        #
        # BarFetchError is already handled below this layer (the service serves
        # the stale cache rather than storing a failure as fact). What reaches
        # here is everything that was never wrapped: an upstream library whose
        # response shape changed, a parser KeyError, an unwrapped timeout.
        bar_failures: dict[tuple[DataSource, str, Timeframe], str] = {}
        for strategy in strategies:
            loaded = loaded_by_id.get(strategy.id)
            if loaded is None or loaded.entry_point != "on_bar":
                continue
            key = (strategy.data_source, strategy.symbol, loaded.timeframe)
            if key in bars_by_key or key in bar_failures:
                continue
            try:
                fetched = service.get_bars(
                    strategy.symbol,
                    loaded.timeframe,
                    strategy.data_source,
                    # 帶 db 才有存量可以退，也才會把抓到的存回去。少了它，
                    # `_prime_from_storage` 第一行就 return——休眠醒來、上游不通的
                    # 那一刻，盯盤這條路拿到的是空清單，而圖表那條路明明有底可以
                    # 垫。反過來，策略用的週期（1wk、1h…）可能從來沒有人看過圖，
                    # 不自己存就永遠沒有底。
                    db=session,
                )
            except Exception as exc:  # noqa: BLE001 -- 一個代號不能拖垮整輪
                bar_failures[key] = f"{type(exc).__name__}: {exc}"
                logger.exception(
                    "bar fetch failed for %s %s", strategy.symbol, loaded.timeframe.value
                )
            else:
                if fetched:
                    bars_by_key[key] = fetched
                else:
                    # **一根都沒有的時候，擋在這裡，不要讓它進到
                    # `_run_bar_strategy`。** 那裡面只看 `len(bars) < warmup`，所以空
                    # 清單會變成兩種結果，兩種都錯：
                    #
                    #   warmup ≥ 1：寫「warming up: 0/3」——一句會讓人以為再等一下
                    #     就好的話，而實際上是行情斷了。
                    #   warmup = 0：`len([]) < 0` 是 False，所以連那句謊話都到不了——
                    #     直接掉進 `bars[-1]` 的 IndexError，走 `_record_strategy_error`，
                    #     連續五次、輪詢五秒一次，**二十五秒後永久停用**，而沒有
                    #     任何東西會把它打開。而「不用寫 Python 就能設定的簡單價格提
                    #     醒」正是這個產品的核心功能，那種策略的 warmup 就是 0。
                    #
                    # 擋在抓取端而不是擋在下游，是因為來源就是「一份空清單被當成答案
                    # 送進去」；在這裡擋，對每一個分支都成立。
                    #
                    # BarFetchError 在服務層就被吞掉換成 stale cache，所以它不會走到
                    # 上面那個 except——要問服務才知道剛才那次抓取成不成功。
                    bar_failures[key] = (
                        "上游沒有回應"
                        if service.bar_fetch_failed(
                            strategy.symbol, loaded.timeframe, strategy.data_source
                        )
                        else "上游沒有這個代號的 K 棒"
                    )

        # 抓不到 K 棒也要看得見。走 _record_feed_problem 是對的（抓不到資料不是使用
        # 者的錯，不累積、不停用），但那條路原本不進任何計數器——於是「報價回得來、
        # K 棒回不來」這個組合在 /healthz 上是全綠的，而每一支 on_bar 策略一則提醒都
        # 沒發出。整組重寫，理由跟心跳那邊一樣：沒被列出來的就是這一輪沒事。
        worker_health.heartbeat.mark_bar_gaps(
            {f"{symbol} {timeframe.value}" for _, symbol, timeframe in bar_failures}
        )

        for strategy in strategies:
            loaded = loaded_by_id.get(strategy.id)
            if loaded is None:
                continue  # failed to compile; already recorded above
            if loaded.entry_point == "on_bar":
                key = (strategy.data_source, strategy.symbol, loaded.timeframe)
                problem = bar_failures.get(key)
                if problem is not None:
                    # 不能拿空清單當答案：那會被下面讀成「還在暖身」，而暖身是
                    # 一句會自己過去的話。抓不到就說抓不到。
                    _record_feed_problem(session, strategy, f"抓不到 K 棒：{problem}")
                    continue
                _run_bar_strategy(
                    session,
                    strategy,
                    loaded,
                    bars_by_key.get(key, []),
                    events,
                    blocked,
                    # 手上有幾根、但不夠暖身，而上游同時斷線——那不是「再等一下就
                    # 好」。空清單那一種已經擋在上面的抓取端了，這是剩下的那一半。
                    feed_failed=service.bar_fetch_failed(
                        strategy.symbol, loaded.timeframe, strategy.data_source
                    ),
                )
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
            _run_tick_strategy(session, strategy, loaded, quote, events, blocked)

        for position in positions:
            quote = quotes.get(position.symbol)
            if quote is not None:
                _check_position_exit(session, position, quote)

        _expire_stale_orders(session, events)

        # Last, and deliberately inside the same try: a notification the owner
        # never received is this product's critical failure, so the sweep runs
        # every poll rather than on a timer of its own. It is bounded by a
        # wall-clock budget as well as a row count -- a count alone was not a
        # bound on time, and twenty rows timing out at ten seconds each is
        # three minutes of this thread not polling a price or checking a
        # stop-loss (see notification/retry.py:_MAX_SWEEP_SEC).
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


def next_poll_delay(db: Session | None = None, at: datetime | None = None) -> float:
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
        if market_calendar.any_open(watched, at):
            return settings.MARKET_DATA_POLL_INTERVAL_SEC
        # **不可以睡過開盤。** 這個迴圈是睡滿一整段才醒的，所以放慢到半小時之後，最
        # 壞情況是 08:35 決定睡 30 分鐘、09:05 才醒——開盤後前五分鐘沒有人在盯，而那
        # 一段正是跳最兇、停損最可能被穿過去的時候。省額度不可以用開盤那幾分鐘去換。
        until_open = market_calendar.seconds_until_next_open(watched, at)
        if until_open is None:
            return CLOSED_POLL_INTERVAL_SEC
        # 下限：時鐘或時區換算有一點點誤差的時候，`max` 讓它不會變成忙碌空轉。
        return max(1.0, min(CLOSED_POLL_INTERVAL_SEC, until_open))
    except Exception:
        logger.exception("could not work out the next poll delay; using the normal interval")
        return settings.MARKET_DATA_POLL_INTERVAL_SEC


def note_downtime_since_last_run(db: Session) -> float | None:
    """開機時回頭看：這個行程起來之前，有多久沒有任何行程在跑。

    ＊ 為什麼行程內的心跳答不出這個問題。

    心跳是這個行程自己的記憶，而這裡要問的正是「上一個行程已經不在了」那段時間。
    行程死掉，心跳跟著歸零，所以醒來之後每一欄都是健康的——那八個小時在那張快照上
    不存在。看門狗也看不到：它去打 `/healthz` 的那一下**就是**把服務叫醒的那一下。

    ＊ 唯一還記得的東西。

    `market_quotes.fetched_at` 是每一輪輪詢都會寫的牆上時鐘，而**關市不會讓它停**
    （`CLOSED_POLL_INTERVAL_SEC`：週期從 5 秒拉長到 300 秒，但照樣抓）。所以「最後
    一次抓到報價是 8 小時前」的意思只有一個：這 8 小時裡沒有行程在跑。

    這一點是整個判斷成立的前提。要是關市完全不抓，每天早上開機都會看起來像睡了 17
    個小時，而這個功能就變成一個每天喊一次狼來了的東西。

    ＊ 沒有東西在盯，就沒有東西被錯過。

    他把策略全部停掉的話，報價本來就不會再更新——那不是停擺，那是沒事做。少了這一
    條，一個空的部署每次重啟都會說自己睡了很久，而那句話是假的。

    **絕不拋出。** 這是在盯盤迴圈起跑之前跑的。為了一句「你剛剛睡著了」而讓迴圈起不
    來，剛好把事情做反。
    """
    try:
        if not _watched_symbols(db):
            return None
        last = db.query(func.max(MarketQuote.fetched_at)).scalar()
        if last is None:
            # 一筆報價都沒有＝這份部署從來沒跑過，不是睡了很久。
            return None
        if last.tzinfo is None:
            # SQLite 存回來是 naive 的。拿它跟 aware 的現在相減會直接炸。
            last = last.replace(tzinfo=UTC)
        slept = (utcnow() - last).total_seconds()
        if slept < DOWNTIME_THRESHOLD_SEC:
            return None
        logger.warning(
            "這個行程起來之前，有 %.0f 分鐘沒有任何行程在跑——那段時間裡的提醒沒有送出。",
            slept / 60,
        )
        worker_health.heartbeat.mark_downtime(slept)
        return slept
    except Exception:
        logger.exception("問不到上一次跑是什麼時候；當成沒有空白")
        return None


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


def _note_downtime_in_its_own_session() -> None:
    """開機那一刻沒有別人的 session 可以借，所以自己開一個、用完就關。"""
    try:
        db = SessionLocal()
    except Exception:
        logger.exception("開不了 session 來問上一次跑是什麼時候；當成沒有空白")
        return
    try:
        note_downtime_since_last_run(db)
    finally:
        db.close()


async def run_forever(stop_event: asyncio.Event) -> None:
    logger.warning(
        "Starting background market-data worker in this process. Run with "
        "--workers 1 -- multiple worker processes would each run their own "
        "loop and duplicate signals for the same tick."
    )
    # 在第一輪之前，不是在第一輪裡面。策略子行程第一次載入沙箱要將近一秒，而輪
    # 詢週期是五秒——不先暖，重啟後的第一輪會被自己的暖機吃掉大半。
    if isinstance(_registry, StrategyPool):
        await asyncio.to_thread(_registry.prewarm)

    # 回頭看一次，在第一輪把 fetched_at 蓋掉之前。免費方案閒置就休眠，而休眠期間一
    # 則提醒都沒送出——醒來之後沒有任何一個探測看得到那段空白（見那個函式的說明）。
    await asyncio.to_thread(_note_downtime_in_its_own_session)

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
        delay = await asyncio.to_thread(next_poll_delay)
        # **在睡之前說，不是醒之後。** 探測最不巧的那一刻正好是在睡的中間，而健康檢查
        # 的門檻是照這個數字放寬的（health.py 的 _max_age）——醒來才說的話，那一整段
        # 睡眠都是拿舊的、短的門檻在量，於是每天半夜都會報一次假的停擺。
        worker_health.heartbeat.expect_next_within(delay)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=delay)
        except TimeoutError:
            pass
    logger.info("market loop stopped")
