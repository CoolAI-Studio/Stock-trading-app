import contextlib
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import SessionLocal
from app.enums import ChannelType, NotificationStatus
from app.models.mixins import utcnow
from app.models.notification import NotificationChannel, NotificationLog
from app.models.order import Order
from app.models.strategy import Strategy
from app.models.user import User
from app.services.events import Event
from app.services.notification.email import EmailSender
from app.services.notification.line import LineSender
from app.services.notification.telegram import TelegramSender
from app.services.notification.webpush import WebPushSender

logger = logging.getLogger("app.notifications")

# How long one "reached nobody" row absorbs later misses. Long enough to bound
# a five-second poll to 24 rows a day rather than 17,280, short enough that the
# ledger still reflects what is happening now rather than this morning.
_NOBODY_FOLD_WINDOW = timedelta(hours=1)

SENDERS = {
    ChannelType.TELEGRAM: TelegramSender(),
    ChannelType.LINE: LineSender(),
    ChannelType.EMAIL: EmailSender(),
    ChannelType.WEB_PUSH: WebPushSender(),
}

_DISPATCHED_EVENT_TYPES = {"order.created", "order.updated", "strategy.error", "strategy.alert"}

# Stamped on an event whose channels the caller already dispatched itself.
# services/alerts.py has to send synchronously -- it needs to know whether
# the owner actually saw the alert before it may start the throttle clock --
# but the event still goes on the bus afterwards for the WS and log
# subscribers. Without this key the bus-subscribed dispatcher would send a
# second copy of every alert.
DISPATCHED_INLINE_KEY = "dispatched_inline"


def mint_receipt_token(channel_type: ChannelType) -> str | None:
    """只有瀏覽器推播那條路有 service worker 可以回報，所以只有它值得發權杖。

    給 Telegram／Email／LINE 發一張，等於發一張永遠兌換不掉的票：那一列會永遠停在
    「沒回報」，而那正好是「真的沒送到」長的樣子——等於把唯一一個看得出通知路徑死掉的
    訊號變成雜訊。
    """
    return secrets.token_urlsafe(32) if channel_type == ChannelType.WEB_PUSH else None


def send_with_receipt(sender, config: dict, message: str, receipt_token: str | None):
    """帶著回條送；沒有回條就照原樣送。

    base.py 的 NotificationSender 協定只有兩個參數，第三個只存在於 WebPushSender 上，
    所以多帶一個給 Telegram 會是 TypeError——而那條路上的 TypeError 會讓那個管道的每
    一則提醒都變成失敗。分岔寫在這裡一次，dispatcher 和 retry 兩條送出路共用。
    """
    if receipt_token is None:
        return sender.send(config, message)
    return sender.send(config, message, receipt_token)


@dataclass
class DispatchResult:
    """What actually reached the user. `delivered == 0` is a failure even when
    nothing raised -- a user with no enabled channel, or one whose only
    channel is filtered out by subscribed_events, was not notified."""

    delivered: int = 0
    failed: int = 0
    # Held for a quiet window and queued for when it ends. Counted apart from
    # `failed` because nothing went wrong -- and apart from `delivered`
    # because the owner has not seen it yet.
    deferred: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.delivered > 0


# 買賣和狀態的說法，跟畫面上用的同一組（frontend OrdersPage 的 SIDE_LABEL /
# STATUS_LABEL）。同一件事在兩個地方叫不同的名字，會讓他以為那是兩件事。
_SIDE_LABEL = {"buy": "買進", "sell": "賣出"}
_STATUS_LABEL = {
    "pending": "待確認",
    "confirmed": "已確認",
    "rejected": "已拒絕",
    "expired": "已過期",
    "failed": "失敗",
}


def _label(mapping: dict[str, str], value) -> str:
    return mapping.get(str(getattr(value, "value", value) or "").lower(), str(value))


def _format_message(event: Event, session=None) -> str:
    """手機上跳出來的那一行。

    ＊ 這是這個產品的產出本身。

    整個系統存在的理由就是為了在事情發生的時候送出這一行，而它是使用者唯一真的會讀
    的東西。原本 order.created 送的是

        New pending order #42 -- review it in the dashboard.

    三個問題：沒有說發生了什麼（他得打開 app 才知道是哪一檔——而提醒的意義正是「不用
    一直看著」）、是英文的（整個產品其他地方都是繁體中文）、而且「pending order」聽起
    來像下單了（這個專案不接券商 API，那一列是提醒紀錄）。

    ＊ 為什麼要 session。

    事件裡只有 order_id。代號、買賣、價格、哪一支策略都在那一列上，所以要撈。不把它
    們塞進事件裡，是因為那份資料會過期：事件在重送佇列裡等的時候，那一列可能已經被
    確認或取消了。

    撈不到就退回只有編號的講法——事件在佇列裡等的時候那一列可能已經被刪掉，而這裡拋
    出去的話，外面那層會把它記成「這個管道整個炸了」。
    """
    if event.type == "order.created":
        order = _load_order(session, event.data.get("order_id"))
        if order is None:
            return f"有一筆新的訊號（#{event.data.get('order_id')}），到畫面上看細節。"
        who = _strategy_name(session, order.strategy_id) or "手動建立"
        return (
            f"{who}：{order.symbol} {_label(_SIDE_LABEL, order.side)}訊號"
            f"，訊號價 {order.signal_price}。"
            "這是提醒，沒有真的下單——要下單請到你的券商 App。"
        )
    if event.type == "order.updated":
        order = _load_order(session, event.data.get("order_id"))
        status = _label(_STATUS_LABEL, event.data.get("status"))
        if order is None:
            return f"訊號 #{event.data.get('order_id')} 變成{status}。"
        return f"{order.symbol} 的訊號變成{status}。"
    if event.type == "strategy.error":
        # 名字，不是 id：「策略 7」對他不是一個可以拿去做事的東西。
        who = _strategy_name(session, event.data.get("strategy_id"))
        who = who or f"策略 {event.data.get('strategy_id')}"
        return (
            f"「{who}」連續出錯太多次，已經被停用——在你修好之前，它不會再發出任何提醒。"
            f"原因：{event.data.get('error')}"
        )
    if event.type == "strategy.alert":
        side = _label(_SIDE_LABEL, event.data.get("side"))
        return (
            f"{event.data.get('strategy_name')}：{event.data.get('symbol')} {side}訊號"
            f"，價格 {event.data.get('price')}。"
            "這是提醒，沒有真的下單——要下單請到你的券商 App。"
        )
    return f"{event.type}: {event.data}"


def _load_order(session, order_id):
    if session is None or order_id is None:
        return None
    try:
        return session.get(Order, order_id)
    except Exception:  # noqa: BLE001 -- 撈不到就用簡短的講法，不要讓一則提醒送不出去
        return None


def _strategy_name(session, strategy_id) -> str | None:
    if session is None or strategy_id is None:
        return None
    try:
        strategy = session.get(Strategy, strategy_id)
    except Exception:  # noqa: BLE001 -- 同上
        return None
    return strategy.name if strategy else None


def handle_event(event: Event, db: Session | None = None) -> DispatchResult:
    """Sync subscriber for services.events.bus (see base.py for why sync is
    fine here). Pass `db` explicitly in tests; production calls (via the
    bus) open and close their own SessionLocal.

    Returns what was delivered. The bus discards the return value; callers
    that need to know whether the user was actually reached (services/alerts.py)
    call this directly."""
    # Checked here, not only at the bus subscription in main.py: services/alerts.py
    # calls this function directly, so a switch applied only at subscribe time
    # would leave alert-only strategies -- the one pipeline that exists purely
    # to notify -- still notifying after the owner turned notifications off.
    if not settings.NOTIFICATIONS_ENABLED:
        return DispatchResult(error="notifications are disabled")

    user_id = event.data.get("user_id")
    if user_id is None or event.type not in _DISPATCHED_EVENT_TYPES:
        return DispatchResult()
    if event.data.get(DISPATCHED_INLINE_KEY):
        return DispatchResult()

    # Imported here rather than at module scope: retry.py reads SENDERS from
    # this module, so a top-level import in both directions is a cycle.
    from app.services.notification import quiet_hours

    result = DispatchResult()
    owns_session = db is None
    session = db or SessionLocal()
    try:
        channels = (
            session.query(NotificationChannel)
            .filter(
                NotificationChannel.user_id == user_id, NotificationChannel.is_enabled.is_(True)
            )
            .all()
        )
        message = _format_message(event, session)

        if not channels:
            # An alert nobody was told about is this product's critical
            # failure, and it used to leave nothing behind at all.
            _record_reaching_nobody(session, user_id, event, message, has_channels=False)
            return result

        order_id = event.data.get("order_id")
        owner = session.get(User, user_id)
        owner_timezone = owner.timezone if owner else quiet_hours.DEFAULT_TIMEZONE

        for channel in channels:
            if channel.subscribed_events and event.type not in channel.subscribed_events:
                continue

            sender = SENDERS.get(channel.channel_type)
            if sender is None:
                continue

            # **趁 session 還好的時候把 id 抄下來。** expire_on_commit 是預設的 True，
            # 所以上一個管道那次 commit 已經把這個物件過期掉了，`channel.id` 是一次
            # 會真的送 SELECT 的讀取。底下的 except 如果在 session 已經進不去的時候
            # 才去讀它，連 `logger.exception(..., channel.id)` 那一行都會拋——那是這
            # 個 except 存在的意義整個被繞過去。
            channel_id = channel.id

            # Everything from here to the commit is wrapped. Only sender.send
            # used to be, so a raise anywhere else -- the quiet-hours
            # calculation, schedule_first_retry, either commit -- escaped the
            # whole loop and the channels after this one were never attempted.
            # Having several channels is meant to be what makes one failing
            # survivable; that made one failing take the rest down with it.
            try:
                outcome = _deliver_to_channel(
                    session, channel, event, message, order_id, user_id, owner_timezone
                )
            except Exception as exc:
                logger.exception("dispatch to channel %s crashed", channel_id)
                # 先記在結果上，再去寫那一列：寫不進去的時候，這一輪至少還說得出
                # 「有一個管道失敗了」。
                result.failed += 1
                result.error = str(exc)
                # Recorded rather than swallowed: an invisible failure is the
                # worse trade for this product. Queued for retry too, because
                # this event fires once and a code fault is no reason to lose
                # it for good.
                _record_crash(session, channel_id, event, message, order_id, user_id, exc)
                continue

            if outcome == "deferred":
                result.deferred += 1
            elif outcome == "sent":
                result.delivered += 1
            else:
                result.failed += 1
                result.error = outcome
            continue

        if result.delivered + result.failed + result.deferred == 0:
            # Channels exist, and every one of them filtered this event type
            # out. Different cause from having no channel, so a different
            # message: telling somebody to create a channel they already have
            # sends them the wrong way.
            _record_reaching_nobody(session, user_id, event, message, has_channels=True)
    finally:
        if owns_session:
            session.close()

    return result


def _record_reaching_nobody(
    session, user_id: int, event: Event, message: str, *, has_channels: bool
) -> None:
    """Leave a row saying this alert reached nobody.

    DispatchResult's own docstring has always said that delivered == 0 is a
    failure even when nothing raised. Nothing acted on it: the dispatcher
    returned, and the 發送紀錄 ledger then looked exactly like an afternoon on
    which nothing had happened. The owner could not find the failure even by
    going and looking for it, which for an alerting product is worse than the
    alert failing loudly.

    channel_id is NULL because there is no channel involved -- that is the
    whole point of the row. No retry is scheduled either: there is nothing to
    retry it TO, and a due date would make the sweep pick it up forever.
    """
    # ONE ROW PER WINDOW, because alerts.py documents alert_interval_sec = 0 as
    # "notify every time" and applies no cap of its own. With no channels that
    # wrote a row on every poll -- 17,280 a day at the five-second default, on a
    # free-tier database, burying the very row this exists to make visible.
    #
    # Folded rather than dropped: the count goes on `attempts`, so the ledger
    # still distinguishes one missed alert from fifty.
    recent = (
        session.query(NotificationLog)
        .filter(
            NotificationLog.user_id == user_id,
            NotificationLog.channel_id.is_(None),
            NotificationLog.created_at >= utcnow() - _NOBODY_FOLD_WINDOW,
        )
        .order_by(NotificationLog.id.desc())
        .first()
    )

    reason = (
        "有啟用中的通知管道，但沒有任何一個訂閱了這個事件類型，所以這則提醒沒有送到任何地方。"
        "請到「通知」頁，在其中一個管道勾選這個事件。"
        if has_channels
        else "沒有任何啟用中的通知管道，所以這則提醒沒有送到任何地方。"
        "請到「通知」頁建立一個管道（Telegram、Email、LINE 或瀏覽器推播都可以）。"
    )
    if recent is not None:
        recent.attempts += 1
        recent.error = f"{reason}（這段期間共有 {recent.attempts} 則提醒沒有送到）"
        session.commit()
        return

    session.add(
        NotificationLog(
            user_id=user_id,
            channel_id=None,
            order_id=event.data.get("order_id"),
            event=event.type,
            status=NotificationStatus.FAILED,
            error=f"{reason}（這段期間共有 1 則提醒沒有送到）",
            # Kept so the row says WHAT went unheard, not merely that something
            # did -- otherwise the owner cannot tell whether it mattered.
            message=message,
            attempts=1,
        )
    )
    session.commit()


def _record_crash(
    session,
    channel_id: int,
    event: Event,
    message: str,
    order_id: int | None,
    user_id: int,
    exc: Exception,
) -> None:
    """把「這個管道整個炸了」寫成一列，而且**這件事本身不可以失敗**。

    上面那個 try 存在的理由，是有好幾個管道就該讓其中一個壞掉還活得下去。可是原本的
    except 第一件事就是 `session.add(log)` ＋ `session.commit()`——如果剛才那個例外發生
    在 session 已經進不去之後（一次 flush 失敗會把它標成必須 rollback；Postgres 上一句
    失敗的語句會讓整個交易中止，接下來每一句都失敗直到 rollback），這次 commit 自己就是
    `PendingRollbackError`，直接穿出 except、穿出迴圈：

      * 後面的管道一個都不會被試——多管道換來的韌性剛好在最需要的那一刻消失
      * 連那一列 FAILED 都寫不下去，所以重送佇列裡也沒有它
      * 這則提醒就這樣不見了，而畫面上什麼都沒有

    所以：先照常寫；失敗了才 rollback 再寫一次。**不先發制人地 rollback**——這個
    session 可能是呼叫端借給我們的（盯盤迴圈就是這樣傳進來的），沒事就 rollback 會把它
    手上還沒送出去的東西一起丟掉。連第二次都寫不進去的話，也只能是「這一個管道記不下
    來」，不可以是「其他管道不用試了」。
    """

    # 跟 handle_event 同一個理由在這裡 import：retry.py 在模組層讀 SENDERS。
    from app.services.notification import retry

    def write() -> None:
        log = NotificationLog(
            user_id=user_id,
            channel_id=channel_id,
            order_id=order_id,
            event=event.type,
            status=NotificationStatus.FAILED,
            error=f"送出時發生未預期的錯誤：{exc}"[:500],
            message=message,
        )
        retry.schedule_first_retry(log)
        session.add(log)
        session.commit()

    try:
        write()
    except Exception:
        logger.exception("could not record the crash for channel %s; retrying once", channel_id)
        try:
            # rollback 之後那個 log 物件已經被踢出 session 了，所以第二次要重新做一個。
            session.rollback()
            write()
        except Exception:
            # 記不下來就記不下來。這裡再拋出去的話，後面的管道就一個都不會被試——
            # 而通知送不到是這個產品的重大失效。
            logger.exception("could not record the crash for channel %s at all", channel_id)
            with contextlib.suppress(Exception):
                session.rollback()


def _deliver_to_channel(
    session,
    channel: NotificationChannel,
    event: Event,
    message: str,
    order_id: int | None,
    user_id: int,
    owner_timezone: str | None,
) -> str:
    """Send one alert through one channel, and say what happened.

    Returns "sent", "deferred", or the error string. Extracted so the caller
    can wrap the WHOLE of it: previously only sender.send was inside a try, and
    a raise anywhere else here -- the quiet-hours calculation, either commit,
    schedule_first_retry -- escaped the per-channel loop and silently skipped
    every channel after this one.
    """
    from app.services.notification import quiet_hours, retry

    sender = SENDERS[channel.channel_type]

    # Held, not dropped. The event that fires at 3am is often the one that
    # mattered most, and the owner's alternative -- switching the channel off
    # so it stops waking them -- is how the warnings stop arriving at all.
    # Reuses the retry queue: the sweep delivers it once the window ends.
    if quiet_hours.is_quiet(channel.quiet_start_hour, channel.quiet_end_hour, owner_timezone):
        due = quiet_hours.window_ends_at(
            channel.quiet_start_hour, channel.quiet_end_hour, owner_timezone
        )
        session.add(
            NotificationLog(
                user_id=user_id,
                channel_id=channel.id,
                order_id=order_id,
                event=event.type,
                status=NotificationStatus.FAILED,
                error=f"靜音時段，將在 {due.astimezone(UTC):%H:%M} UTC 之後送出",
                message=message,
                attempts=0,
                next_retry_at=due,
            )
        )
        session.commit()
        return "deferred"

    # 真正的提醒也要帶回條，不能只有那顆「測試」按鈕帶。
    #
    # RFC 8030 §5 原文寫著推播服務回 2xx「does not indicate that the message was
    # delivered to the user agent」。所以在裝置回報之前，一支被 iOS 悄悄收回通知權限的
    # 手機，跟一支好好收到的手機，在這個 app 裡長得一模一樣：都是 SENT、都沒有錯誤、管
    # 道都還寫著「啟用中」。回條的機制早就整條建好了（權杖進加密內容、sw.js 顯示完就
    # POST 回來、delivered_at 記那一刻），卻只有 /test 端點會發權杖——等於他唯一問得出
    # 「我的提醒真的有到嗎」的地方，是一顆要自己去按的按鈕。而警告真的停擺的時候，沒有
    # 人會想到要去按它。
    receipt_token = mint_receipt_token(channel.channel_type)
    try:
        send_result = send_with_receipt(sender, channel.config_encrypted, message, receipt_token)
    except Exception as exc:
        logger.exception("notification send crashed for channel %s", channel.id)
        ok, error = False, str(exc)
    else:
        ok, error = send_result.ok, send_result.error

    log = NotificationLog(
        user_id=user_id,
        channel_id=channel.id,
        order_id=order_id,
        event=event.type,
        status=NotificationStatus.SENT if ok else NotificationStatus.FAILED,
        error=error,
        # Kept even on success, so the row says what the owner was actually
        # told rather than only that something was sent.
        message=message,
        # 送出去的那張權杖要記在這一列上，否則裝置回報時對不到任何一列，而
        # delivered_at 就永遠是 NULL——等於發了權杖卻沒有人收得下。
        #
        # 送失敗就不記：那張權杖從來沒有離開過這台伺服器，留著只會讓一列失敗的紀錄
        # 看起來像「還在等裝置回報」。
        receipt_token=receipt_token if ok else None,
    )
    if not ok:
        # order.created and strategy.error fire once and never again, so
        # without a due time here a ten-second provider outage loses them for
        # good. services/notification/retry.py sweeps what this queues.
        retry.schedule_first_retry(log)
    session.add(log)
    channel.last_sent_at = utcnow()
    channel.last_error = error
    session.commit()

    return "sent" if ok else (error or "unknown error")
