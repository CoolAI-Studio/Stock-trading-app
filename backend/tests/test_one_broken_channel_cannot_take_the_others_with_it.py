"""一個管道出事，不可以把同一則提醒的其他管道一起帶走。

有好幾個管道，本來就是為了讓其中一個壞掉還survivable——dispatcher 裡那段註解自己寫
著這件事，而且它就是為此把整個 `_deliver_to_channel` 包進 try 裡的（原本只包
`sender.send`，所以靜音時段的計算、`schedule_first_retry`、任何一次 commit 只要拋出
來，後面的管道就一個都不會被試）。

**可是那個 except 自己也會拋。** 它做的第一件事是 `session.add(log)` ＋
`session.commit()`，而如果剛才那個例外是在 session 已經進入失敗狀態之後才丟出來的
（一次資料庫抖動、一次逾時的連線——免費方案上這是會發生的），這次 commit 就是
`PendingRollbackError`，直接穿出 except、穿出迴圈：

  * 後面的管道一個都不會被試——**多管道換來的韌性剛好在最需要的那一刻消失**
  * 連那一列 FAILED 都寫不下去，所以重送佇列裡也沒有它
  * 這則提醒就這樣不見了，而畫面上什麼都沒有

通知送不到是這個產品的重大失效（CLAUDE.md 第一句）。所以記錄失敗這件事本身也必須是
不會失敗的：先 rollback 把 session 救回來，再寫那一列；連寫都寫不進去的話，也只能是
「這一個管道記不下來」，不可以是「其他管道不用試了」。
"""

from unittest.mock import MagicMock, patch

from app.enums import ChannelType, NotificationStatus
from app.models.notification import NotificationChannel, NotificationLog
from app.models.user import User
from app.services.events import Event
from app.services.notification import dispatcher
from app.services.notification.dispatcher import handle_event


def _user(db_session) -> User:
    user = User(email="two-channels@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _channel(db_session, user: User, label: str) -> NotificationChannel:
    channel = NotificationChannel(
        user_id=user.id,
        channel_type=ChannelType.TELEGRAM,
        label=label,
        config_encrypted={"bot_token": "t", "chat_id": "123"},
        is_enabled=True,
    )
    db_session.add(channel)
    db_session.commit()
    db_session.refresh(channel)
    return channel


def _telegram_ok() -> MagicMock:
    response = MagicMock(status_code=200)
    response.json.return_value = {"ok": True}
    response.raise_for_status.return_value = None
    return response


def test_a_channel_that_fails_with_a_poisoned_session_does_not_stop_the_next_one(
    db_session, monkeypatch
):
    """第一個管道在 session 已經壞掉之後才丟例外——第二個管道還是要收到。

    「session 已經壞掉」是真的做出來的，不是假裝的：一次失敗的 flush 就會把這個
    session 標成必須 rollback，接下來任何一次 commit 都是 PendingRollbackError。那正
    是資料庫抖一下的時候，例外真正會有的形狀（線上的 Postgres 更寬：一句失敗的
    語句就會讓整個交易中止，接下來每一句都失敗直到 rollback）。

    而第一個撞上這件事的不是 commit，是那句
    logger.exception(..., channel.id)——expire_on_commit 是預設的 True，所以讀
    channel.id 會真的送一句 SELECT。except 的第一行就拋了，這個 except 存在的意義
    整個被繞過去。
    """
    user = _user(db_session)
    broken = _channel(db_session, user, "壞掉的那個")
    healthy = _channel(db_session, user, "好的那個")

    real_deliver = dispatcher._deliver_to_channel

    def poison_then_raise(session, channel, *args, **kwargs):
        if channel.id != broken.id:
            return real_deliver(session, channel, *args, **kwargs)
        session.add(NotificationLog(user_id=None, event="x", status=NotificationStatus.FAILED))
        try:
            session.flush()
        except Exception:
            # 吞掉，但 session 已經被標成必須 rollback 了——這就是重點。
            pass
        raise RuntimeError("送出的時候資料庫斷了")

    monkeypatch.setattr(dispatcher, "_deliver_to_channel", poison_then_raise)

    with patch("httpx.post", return_value=_telegram_ok()):
        handle_event(
            Event(type="order.created", data={"order_id": 1, "user_id": user.id}), db=db_session
        )

    logs = {log.channel_id: log for log in db_session.query(NotificationLog).all()}

    assert healthy.id in logs, (
        "第一個管道壞掉，第二個管道連試都沒有被試——多管道換來的韌性剛好在最需要的那一刻消失了。"
    )
    assert logs[healthy.id].status == NotificationStatus.SENT

    assert broken.id in logs, "壞掉的那個連一列 FAILED 都沒寫下來，所以重送佇列裡也沒有它"
    assert logs[broken.id].status == NotificationStatus.FAILED
    assert logs[broken.id].error
    # 重送要排得進去。排不進去的話那則提醒就真的沒了——這一則事件只發生一次。
    assert logs[broken.id].next_retry_at is not None


def test_a_plain_crash_is_recorded_without_touching_the_callers_session(db_session, monkeypatch):
    """管道炸掉、但 session 好好的——那一列要照常寫下去，而且不可以順手 rollback。

    這條守兩件事：

    一、**常見的那條路還在。** 記錄失敗的時候如果不小心寫壞（例如少 import 一個名
        字），只有這條會紅——上面那條走的是救援路徑，救援路徑本身有 try 包著，會把
        寫壞這件事吞成一行 log。

    二、**不可以先發制人地 rollback。** 這個 session 可能是呼叫端借給我們的（盯盤迴
        圈就是這樣傳進來的），沒事就 rollback 會把它手上還沒送出去的東西一起丟掉，
        而那可能正是那一輪的訊號和 Order。
    """
    user = _user(db_session)
    channel = _channel(db_session, user, "會炸的那個")
    # 呼叫端手上還沒送出去的東西。
    db_session.add(User(email="pending@example.com", hashed_password="x"))

    def just_raise(*args, **kwargs):
        raise RuntimeError("送出的時候炸了")

    monkeypatch.setattr(dispatcher, "_deliver_to_channel", just_raise)

    handle_event(
        Event(type="order.created", data={"order_id": 1, "user_id": user.id}), db=db_session
    )

    log = db_session.query(NotificationLog).filter(NotificationLog.channel_id == channel.id).one()
    assert log.status == NotificationStatus.FAILED
    assert log.next_retry_at is not None

    survived = db_session.query(User).filter(User.email == "pending@example.com").count()
    assert survived == 1, "session 沒壞卻被 rollback 了，呼叫端還沒送出去的東西一起沒了"
