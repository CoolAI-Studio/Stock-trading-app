"""A notification that failed to send has to be tried again.

This product's whole job is to tell the owner something happened. An alert
that does not arrive is its one unaffordable failure -- worse than a missing
order type, worse than a wrong number on a page, because the owner cannot
even tell it happened.

Alert-only strategies already survive a blip, but only by accident of shape:
the strategy re-fires every tick, so the *next* signal retries the delivery.
Order and strategy-error notifications fire once. A ten-second Telegram
outage, one SMTP timeout, and the pending-order notice was gone for good --
the two events that most need to arrive were the two with no second chance.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from app.enums import ChannelType, NotificationStatus, OrderSide, OrderSource, OrderStatus
from app.models.mixins import utcnow
from app.models.notification import NotificationChannel, NotificationLog
from app.models.order import Order
from app.models.user import User
from app.services.notification import retry
from app.services.notification.base import SendResult

MESSAGE = "有新的待確認訂單：AAPL 買進 10"


def _user(db_session) -> User:
    user = User(email="retry@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _channel(db_session, user: User) -> NotificationChannel:
    channel = NotificationChannel(
        user_id=user.id,
        channel_type=ChannelType.TELEGRAM,
        label="phone",
        config_encrypted={"bot_token": "t", "chat_id": "1"},
        is_enabled=True,
    )
    db_session.add(channel)
    db_session.commit()
    db_session.refresh(channel)
    return channel


def _failed_log(db_session, user, channel, *, due_in_sec: int = -1, attempts: int = 1):
    log = NotificationLog(
        user_id=user.id,
        channel_id=channel.id,
        event="order.created",
        status=NotificationStatus.FAILED,
        error="Telegram timed out",
        message=MESSAGE,
        attempts=attempts,
        next_retry_at=utcnow() + timedelta(seconds=due_in_sec),
    )
    db_session.add(log)
    db_session.commit()
    db_session.refresh(log)
    return log


def _sending(result: SendResult):
    return patch.object(retry.SENDERS[ChannelType.TELEGRAM], "send", return_value=result)


def _seconds_until(when) -> float:
    # next_retry_at comes back naive from SQLite, so compare like with like.
    return (when.replace(tzinfo=None) - utcnow().replace(tzinfo=None)).total_seconds()


def test_a_failed_notification_is_sent_again_when_it_comes_due(db_session):
    user = _user(db_session)
    channel = _channel(db_session, user)
    log = _failed_log(db_session, user, channel)

    with _sending(SendResult(ok=True)):
        retry.retry_pending(db_session)

    db_session.refresh(log)
    assert log.status == NotificationStatus.SENT
    assert log.next_retry_at is None, "delivered, so nothing more is owed"
    assert log.attempts == 2


def test_the_original_message_is_what_gets_resent(db_session):
    """Not a placeholder: the event that produced it is long gone by the time
    the retry runs, so the rendered text has to have been kept."""
    user = _user(db_session)
    channel = _channel(db_session, user)
    _failed_log(db_session, user, channel)

    with _sending(SendResult(ok=True)) as send:
        retry.retry_pending(db_session)

    assert send.call_args.args[1] == MESSAGE


def test_a_notification_not_yet_due_is_left_alone(db_session):
    """The backoff is the whole point -- retrying every poll would hammer a
    dead endpoint five times a minute."""
    user = _user(db_session)
    channel = _channel(db_session, user)
    _failed_log(db_session, user, channel, due_in_sec=300)

    with _sending(SendResult(ok=True)) as send:
        retry.retry_pending(db_session)

    assert send.call_count == 0


def test_each_failure_pushes_the_next_attempt_further_out(db_session):
    user = _user(db_session)
    channel = _channel(db_session, user)
    log = _failed_log(db_session, user, channel, attempts=1)

    with _sending(SendResult(ok=False, error="503")):
        retry.retry_pending(db_session)

    db_session.refresh(log)
    assert log.attempts == 2
    assert log.status == NotificationStatus.FAILED
    first_gap = _seconds_until(log.next_retry_at)

    log.next_retry_at = utcnow() - timedelta(seconds=1)
    db_session.commit()
    with _sending(SendResult(ok=False, error="503")):
        retry.retry_pending(db_session)

    db_session.refresh(log)
    assert log.attempts == 3
    assert _seconds_until(log.next_retry_at) > first_gap, (
        "backoff has to grow, or a dead channel is polled forever"
    )


def test_retrying_stops_after_the_bound_rather_than_forever(db_session):
    """A revoked bot token never recovers on its own. Past the bound the row
    stays FAILED with no next attempt, which is also what makes it visible as
    a broken channel rather than an endless queue."""
    user = _user(db_session)
    channel = _channel(db_session, user)
    log = _failed_log(db_session, user, channel, attempts=retry.MAX_ATTEMPTS)

    with _sending(SendResult(ok=False, error="401")) as send:
        retry.retry_pending(db_session)

    db_session.refresh(log)
    assert send.call_count == 0, "already at the bound"
    assert log.next_retry_at is None
    assert log.status == NotificationStatus.FAILED


def test_a_disabled_channel_is_not_retried(db_session):
    user = _user(db_session)
    channel = _channel(db_session, user)
    log = _failed_log(db_session, user, channel)
    channel.is_enabled = False
    db_session.commit()

    with _sending(SendResult(ok=True)) as send:
        retry.retry_pending(db_session)

    db_session.refresh(log)
    assert send.call_count == 0
    assert log.next_retry_at is None, "the owner switched it off; stop owing them this"


def test_a_row_from_before_the_message_was_recorded_is_not_retried(db_session):
    """Existing rows have no message to resend. Skipping them beats inventing
    text and sending the owner something that never happened."""
    user = _user(db_session)
    channel = _channel(db_session, user)
    log = _failed_log(db_session, user, channel)
    log.message = None
    db_session.commit()

    with _sending(SendResult(ok=True)) as send:
        retry.retry_pending(db_session)

    assert send.call_count == 0
    db_session.refresh(log)
    assert log.next_retry_at is None


def test_a_delivery_failure_is_queued_for_retry_when_it_first_happens(db_session):
    """The dispatcher is where the queue starts: without it setting a due
    time, nothing the sweep looks for ever exists."""
    from app.services.events import Event
    from app.services.notification import dispatcher

    user = _user(db_session)
    channel = _channel(db_session, user)
    order = Order(
        user_id=user.id,
        source=OrderSource.MANUAL,
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal(10),
        status=OrderStatus.PENDING,
    )
    db_session.add(order)
    db_session.commit()

    with patch.object(
        dispatcher.SENDERS[ChannelType.TELEGRAM],
        "send",
        return_value=SendResult(ok=False, error="timed out"),
    ):
        dispatcher.handle_event(
            Event(type="order.created", data={"user_id": user.id, "order_id": order.id}),
            db=db_session,
        )

    log = db_session.query(NotificationLog).filter(NotificationLog.channel_id == channel.id).one()
    assert log.status == NotificationStatus.FAILED
    assert log.next_retry_at is not None, "a failure with no due time is a failure nobody retries"
    assert log.message, "the rendered text has to be kept or there is nothing to resend"
    assert log.attempts == 1


def test_a_delivered_notification_is_not_queued(db_session):
    from app.services.events import Event
    from app.services.notification import dispatcher

    user = _user(db_session)
    channel = _channel(db_session, user)

    with patch.object(
        dispatcher.SENDERS[ChannelType.TELEGRAM], "send", return_value=SendResult(ok=True)
    ):
        dispatcher.handle_event(
            Event(type="order.created", data={"user_id": user.id, "order_id": None}), db=db_session
        )

    log = db_session.query(NotificationLog).filter(NotificationLog.channel_id == channel.id).one()
    assert log.status == NotificationStatus.SENT
    assert log.next_retry_at is None


# --- 憑證失效要被認出來，而「訊息太長」不是憑證失效 ---------------------------
#
# retry.py 的註解說「the 'HTTP <code>' prefix every sender now emits」。那句話對
# telegram 和 webpush 成立，對 **LINE 和 Email 不成立**——它們直接回 `str(exc)`。
#
# 後果是靜默的、而且方向最壞：憑證失效永遠不會被判成永久失敗，所以管道不會停用、不會
# 透過其他管道告訴他、畫面上永遠寫著「啟用中」，而每一則提醒都送進黑洞。這個 repo 的
# 最高優先就是這種東西。


def test_a_line_token_that_stopped_working_is_recognised():
    """LINE 的 access token 過期或被撤銷 → 401。

    那要停用管道並告訴他去哪裡換一把，而不是每十分鐘重試一次直到永遠。
    """
    import httpx

    from app.services.notification import retry
    from app.services.notification.line import LineSender

    request = httpx.Request("POST", "https://api.line.me/v2/bot/message/push")
    exc = httpx.HTTPStatusError(
        "Client error", request=request, response=httpx.Response(401, request=request)
    )

    assert retry._is_permanent(LineSender()._describe(exc)), (
        "LINE 的憑證失效沒有被認出來——管道會一直寫著「啟用中」而每則提醒都消失"
    )


def test_an_smtp_password_that_stopped_working_is_recognised():
    """Email 的應用程式密碼被撤銷 → SMTP 535。

    這個特別常見：Gmail 的應用程式密碼會因為改密碼、關閉兩步驟驗證而失效。
    """
    import smtplib

    from app.services.notification import retry
    from app.services.notification.email import EmailSender

    exc = smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")

    assert retry._is_permanent(EmailSender()._describe(exc)), (
        "SMTP 的認證失敗沒有被認出來——他改過一次密碼之後就再也收不到 Email 提醒"
    )


def test_a_temporary_smtp_problem_is_not_permanent():
    """而暫時性的（對方忙、連線斷）要繼續重試，不可以把管道關掉。"""
    import smtplib

    from app.services.notification import retry
    from app.services.notification.email import EmailSender

    for exc in (
        smtplib.SMTPServerDisconnected("connection lost"),
        smtplib.SMTPResponseException(451, b"4.3.0 Temporary failure"),
    ):
        assert not retry._is_permanent(EmailSender()._describe(exc)), f"{exc} 不應該永久停用管道"


def test_a_message_that_was_too_long_does_not_disable_telegram():
    """**Telegram 的 400 有兩種意思，而它們的處置相反。**

    400 是「機器人權杖不對」，也是「訊息太長」。現在兩個都被當成憑證錯誤而永久停用管
    道，並叫他去檢查權杖——而權杖是好的。一則過長的策略錯誤訊息就足以把他的 Telegram
    永久關掉。
    """
    from app.services.notification import retry

    assert not retry._is_permanent("HTTP 400: Bad Request: message is too long"), (
        "訊息太長把管道永久停用了，而那是我們自己送出去的內容造成的"
    )
    # 而真正的憑證錯誤還是要停。
    assert retry._is_permanent("HTTP 400: Bad Request: bot token is invalid")
    assert retry._is_permanent("HTTP 401: Unauthorized")


def test_a_long_alert_is_trimmed_before_it_is_sent():
    """上一條的另一半：**根本不該送出過長的訊息**。

    webpush 有 MAX_BODY_CHARS（600），telegram 和 line 一個都沒有。而策略的錯誤訊息可
    以很長（Python 的例外字串、使用者自己寫的 message），所以那個 400 是我們自己造成
    的。分類修好只是不再把管道關掉；不送出過長的訊息才是讓那則提醒真的送到。

    Telegram 的上限是 4096 字元，LINE 是 5000。取比較小的那一個當共用上限，因為同一則
    提醒會走每一個管道，而截斷點不一致只會讓比對變難。
    """
    from app.services.notification.line import LineSender
    from app.services.notification.telegram import TelegramSender

    long_message = "跌破 900：" + "詳細說明" * 2000

    for sender in (TelegramSender(), LineSender()):
        fitted = sender._fit(long_message)
        assert len(fitted) < len(long_message), f"{type(sender).__name__} 沒有截斷過長的訊息"
        assert len(fitted) <= 4096, f"{type(sender).__name__} 截完還是超過 Telegram 的上限"
        # 截斷要看得出來，否則他會以為那就是全部。
        assert fitted.endswith("…") or "…" in fitted[-10:]
        # 最重要的那一段在開頭，不可以被截掉。
        assert fitted.startswith("跌破 900：")
