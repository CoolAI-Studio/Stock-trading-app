"""送達回條只發給那顆「測試」按鈕，真正的提醒一則都沒有。

回條這條管線早就整條建好了：權杖放進**加密過**的推播內容、service worker 顯示完
通知就 POST 回來、`delivered_at` 記下那一刻、`/logs/{id}` 讓畫面問得到。可是會發
權杖的只有 `POST /channels/{id}/test` 一個地方——真正的提醒走的是 dispatcher 和
retry sweep，那兩條路呼叫 `sender.send` 時都只給兩個參數，所以那些推播的內容裡沒
有 `receipt`，`delivered_at` 也就永遠是 NULL。

**後果不是少一個統計。** RFC 8030 §5 原文就寫著推播服務回 2xx「does not indicate
that the message was delivered to the user agent」。所以在裝置回報之前，一支被 iOS
悄悄收回通知權限（或訂閱已經被丟掉）的手機，跟一支好好收到的手機，在這個 app 裡長
得一模一樣：兩邊都是 SENT、兩邊都沒有錯誤、管道都還寫著「啟用中」。使用者唯一問得
出真相的儀器，是那顆要他自己去按的「測試」按鈕——而警告真的停擺的時候，沒有人會想
到要去按它。

這裡問的是行為（那則**真的**提醒帶著回條出門了嗎；裝置回報之後那一列變成已送達了
嗎），不是實作細節。
"""

import json
from unittest.mock import patch

from app.enums import ChannelType, NotificationStatus
from app.models.mixins import utcnow
from app.models.notification import NotificationChannel, NotificationLog
from app.models.user import User
from app.services.events import Event
from app.services.notification import retry
from app.services.notification.dispatcher import handle_event

SUBSCRIPTION = {
    "endpoint": "https://web.push.apple.com/real-alert",
    "p256dh": "p256dh-value",
    "auth": "auth-value",
}


def _user(db_session) -> User:
    user = User(email="real-alert@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _push_channel(db_session, user_id: int) -> NotificationChannel:
    channel = NotificationChannel(
        user_id=user_id,
        channel_type=ChannelType.WEB_PUSH,
        label="iphone",
        config_encrypted=dict(SUBSCRIPTION),
        is_enabled=True,
    )
    db_session.add(channel)
    db_session.commit()
    db_session.refresh(channel)
    return channel


def _dispatch_one_alert(db_session, user_id: int) -> dict:
    """送一則真的提醒，把 pywebpush 實際收到的推播內容交回來。

    走 handle_event 而不是伸手進 WebPushSender：這條路才是真實提醒走的路，而缺口
    正好在這條路上。
    """
    with patch("app.services.notification.webpush.webpush", return_value=None) as mock:
        handle_event(
            Event(type="order.created", data={"order_id": 1, "user_id": user_id}), db=db_session
        )
    return json.loads(mock.call_args.kwargs["data"]) if mock.call_args else {}


# --- dispatcher：第一次送出 --------------------------------------------------


def test_a_real_alert_carries_a_receipt_token_too(db_session):
    """真正的提醒也要帶著回條出門，不然裝置根本沒有東西可以回報。"""
    user = _user(db_session)
    _push_channel(db_session, user.id)

    payload = _dispatch_one_alert(db_session, user.id)

    assert payload.get("receipt"), "真的提醒沒帶回條，裝置無從回報，delivered_at 永遠是 NULL"


def test_the_token_is_recorded_against_the_alerts_own_row(db_session):
    """權杖要對得回那一列，否則裝置回報了也沒有東西會被標成已送達。"""
    user = _user(db_session)
    channel = _push_channel(db_session, user.id)

    payload = _dispatch_one_alert(db_session, user.id)

    log = db_session.query(NotificationLog).one()
    assert log.channel_id == channel.id
    assert log.status == NotificationStatus.SENT
    assert log.receipt_token, "那一列沒有權杖，回報進來也對不到任何東西"
    assert log.receipt_token == payload.get("receipt")
    assert log.delivered_at is None, "還沒有任何裝置回報過"


def test_the_device_reporting_back_marks_the_real_alert_delivered(client, db_session, monkeypatch):
    """整條路走一次：真的提醒 → 裝置回報 → 那一列變成已送達。

    conftest 的 client fixture 會把 NOTIFICATIONS_ENABLED 關掉（免得測試真的送
    東西出去），而這條測試就是要走 dispatcher，所以要明講把它打開。
    client 沒有帶 Authorization：service worker 本來就沒有登入。
    """
    monkeypatch.setattr("app.config.settings.NOTIFICATIONS_ENABLED", True)
    user = _user(db_session)
    _push_channel(db_session, user.id)

    payload = _dispatch_one_alert(db_session, user.id)

    resp = client.post("/api/notifications/push/receipt", json={"token": payload.get("receipt")})

    assert resp.status_code == 204
    log = db_session.query(NotificationLog).one()
    assert log.delivered_at is not None, "裝置說它顯示了，這一列卻還是「沒回報」"


def test_a_send_that_never_left_leaves_no_redeemable_token(db_session, monkeypatch):
    """送失敗的那一次不留權杖：沒有東西兌換得掉它，留著只是佔住一個 unique 值。"""
    # 只設公鑰不設私鑰，vapid_keys 就會回一個沒有私鑰的答案，推播在真正送出去之前
    # 就停住。刻意不去動 SECRET_ENCRYPTION_KEY——管道的設定是用它加密的，清掉它會讓
    # 這條測試死在跟這個缺口無關的地方。
    monkeypatch.setattr("app.config.settings.VAPID_PUBLIC_KEY", "public-key-only")
    monkeypatch.setattr("app.config.settings.VAPID_PRIVATE_KEY", "")
    user = _user(db_session)
    _push_channel(db_session, user.id)

    with patch("app.services.notification.webpush.webpush", return_value=None):
        handle_event(
            Event(type="order.created", data={"order_id": 1, "user_id": user.id}), db=db_session
        )

    log = db_session.query(NotificationLog).one()
    assert log.status == NotificationStatus.FAILED
    assert log.receipt_token is None


def test_a_non_push_channel_is_not_handed_an_argument_it_cannot_take(db_session):
    """Telegram／Email／LINE 那邊沒有 service worker 可以回報。

    兩件事都要成立：不能發一張永遠兌換不掉的票（那一列會永遠停在「沒回報」，而那
    正好是「真的沒送到」長的樣子），而且**它們的 send 只吃兩個參數**——多帶一個過
    去就是 TypeError，那會把這個管道的每一則提醒都變成失敗。
    """
    user = _user(db_session)
    db_session.add(
        NotificationChannel(
            user_id=user.id,
            channel_type=ChannelType.TELEGRAM,
            label="tg",
            config_encrypted={"bot_token": "t", "chat_id": "1"},
            is_enabled=True,
        )
    )
    db_session.commit()

    with patch("app.services.notification.telegram.TelegramSender.send") as send:
        send.return_value.ok = True
        send.return_value.error = None
        handle_event(
            Event(type="order.created", data={"order_id": 1, "user_id": user.id}), db=db_session
        )

    assert send.call_args is not None, "Telegram 那則根本沒送出去"
    assert len(send.call_args.args) + len(send.call_args.kwargs) == 2
    log = db_session.query(NotificationLog).one()
    assert log.receipt_token is None


# --- retry sweep：重送的那一次 ------------------------------------------------


def test_a_retried_alert_carries_a_receipt_token_as_well(db_session):
    """重送成功的那一次同樣要能被確認。

    這條路特別重要：會走到重送，代表第一次已經沒送出去過一次了——那正是最需要知道
    「這次到底有沒有到」的時候。
    """
    user = _user(db_session)
    channel = _push_channel(db_session, user.id)
    log = NotificationLog(
        user_id=user.id,
        channel_id=channel.id,
        event="order.created",
        status=NotificationStatus.FAILED,
        error="HTTP 503",
        message="2330.TW 跌破 900",
        attempts=1,
        next_retry_at=utcnow(),
    )
    db_session.add(log)
    db_session.commit()

    with patch("app.services.notification.webpush.webpush", return_value=None) as mock:
        retry.retry_pending(db_session)

    payload = json.loads(mock.call_args.kwargs["data"]) if mock.call_args else {}
    db_session.refresh(log)
    assert log.status == NotificationStatus.SENT
    assert payload.get("receipt"), "重送出去的那一則沒帶回條"
    assert log.receipt_token == payload.get("receipt")
