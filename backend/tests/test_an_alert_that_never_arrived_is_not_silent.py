"""一則沒有送到的提醒，是這個產品的重大失效——所以它不可以只在畫面上。

CLAUDE.md 第一段：「通知沒送到，是這個產品的重大失效；少一個委託類型不是。」

而 `/healthz` 的 `notifications` 那一格，在這個檔案出現之前只問了一件事：

    NOTIFICATIONS_ENABLED 是不是開著。

也就是說它問的是「**這個功能有沒有被關掉**」，不是「**提醒有沒有送到**」。

於是這個情境是全綠的：

    他的 Telegram bot token 被撤銷（或 SMTP 密碼改了、或推播訂閱過期）
    → 每一則提醒都失敗
    → 重送到期，放棄
    → /healthz：全綠（通知功能「啟用中」）
    → 看門狗：永遠不寄信
    → 而他什麼都不知道，直到自己想起來打開狀態頁

＊ 這跟前面兩次是同一個形狀。

子行程全面停擺（#18 之後補的 strategy_blocked_sec）、K 棒抓不到（#67 的
bar_gap_sec）——兩次都是「後果真的發生了，而每一個探測都是綠的」。這一次是同一件
事，只是輪到最重要的那一格：**提醒本身**。

＊ 為什麼是「放棄」而不是「失敗」。

失敗會重送，而重送多半會成功——一次 Telegram 抖動不該把看門狗叫起來（那是
test_the_watchdog_does_not_cry_wolf 守的事）。**放棄**不一樣：重送已經用完，那一則
提醒永遠不會到了。那不是雜訊，那正是這個產品唯一不能發生的事。

＊ 數字從資料庫來，但**不是每次探測都去數**（#100 改的）。

原本這裡寫的理由是「/healthz 本來就會碰資料庫，多一句有索引的計數不會改變它的成本結
構」。那個前提在 #98、#99 之後被抽掉了地基：淺層探測現在什麼都不碰，所以這「一句」
就是全部的成本——而平台的健康檢查每幾秒就打一次。量出來是每分鐘 15.4 句 SQL，也就是
免費方案的運算單元永遠不休眠、額度月中用完，接下來半個月一則提醒都不會送出。

所以改成由盯盤迴圈在它自己那一輪裡數好（那一輪本來就在用資料庫），探測讀心跳。數字
還是從資料庫來的，「行程重啟就忘了」也還是解掉的——每一輪都重新數一次，而第一輪在開
機後幾秒內就跑完。

因此**這個檔案裡的斷言問的是 `?deep=1`**：那是看門狗在看的那一條，也是這一格存在的
理由。沒帶參數的那一條在還沒有人數過的時候回 `skipped`——「不知道」不可以顯示成「沒
問題」。
"""

from datetime import timedelta

import pytest

from app.config import settings
from app.enums import ChannelType, NotificationStatus
from app.models.mixins import utcnow
from app.models.notification import NotificationChannel, NotificationLog
from app.models.user import User


@pytest.fixture(autouse=True)
def _notifications_on(client, monkeypatch):
    """conftest 的 `client` 會把通知關掉（大部分測試不想真的送東西）。

    **要在它之後才設回來**，不然這一整組會在「功能被關掉」那一格上全紅——而那會讓
    第一條測試變成假綠燈：它斷言 notifications 是 fail，而那本來就是 fail，跟它想測
    的「有一則提醒放棄了」一點關係都沒有。（第一版就是這樣寫的。）
    """
    monkeypatch.setattr(settings, "NOTIFICATIONS_ENABLED", True)


def _owner(db_session) -> User:
    user = User(email="undelivered@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _channel(db_session, user: User) -> NotificationChannel:
    channel = NotificationChannel(
        user_id=user.id,
        channel_type=ChannelType.TELEGRAM,
        label="被撤銷的那個",
        config_encrypted={"bot_token": "t", "chat_id": "1"},
        is_enabled=True,
    )
    db_session.add(channel)
    db_session.commit()
    db_session.refresh(channel)
    return channel


def _log(db_session, user, channel, *, given_up: bool, hours_ago: float = 1.0) -> NotificationLog:
    log = NotificationLog(
        user_id=user.id,
        channel_id=channel.id if channel else None,
        event="order.created",
        status=NotificationStatus.FAILED,
        error="HTTP 401",
        message="x",
        attempts=5,
        # 放棄＝不會再重送。還在重送的那些有 next_retry_at。
        next_retry_at=None if given_up else utcnow() + timedelta(minutes=5),
        created_at=utcnow() - timedelta(hours=hours_ago),
    )
    db_session.add(log)
    db_session.commit()
    return log


def test_a_given_up_alert_turns_the_probe_red(client, db_session):
    """重送用完了還是沒送到——那一則提醒永遠不會到了，而這是唯一會主動說話的地方。"""
    user = _owner(db_session)
    _log(db_session, user, _channel(db_session, user), given_up=True)

    # **看門狗看的是深的那一條。** `/healthz` 沒帶參數的時候只回答「重開這台機器有沒有
    # 機會修好」——Render 的健康檢查看的是它，而它失敗 60 秒就會把行程重開（見
    # test_the_probe_render_watches_cannot_restart_him_forever）。這裡問的是「有沒有人
    # 會被通知」，那是 ?deep=1。
    response = client.get("/healthz", params={"deep": "1"})

    assert response.status_code == 503
    assert response.json()["checks"]["notifications"]["status"] == "fail"


def test_it_counts_them_but_does_not_say_whose(client, db_session):
    """/healthz 沒有憑證也打得到，所以只說幾則——跟代號和策略那兩格同一條規則。"""
    user = _owner(db_session)
    channel = _channel(db_session, user)
    _log(db_session, user, channel, given_up=True)
    _log(db_session, user, channel, given_up=True)

    body = client.get("/healthz", params={"deep": "1"}).json()

    assert body["checks"]["notifications"]["undelivered"] == 2
    assert "undelivered@example.com" not in response_text(body)
    assert "被撤銷的那個" not in response_text(body)


def response_text(body) -> str:
    import json

    return json.dumps(body, ensure_ascii=False)


def test_something_still_being_retried_is_not_an_outage(client, db_session):
    """還在重送的不算。

    一次 Telegram 抖動不該把看門狗叫起來——那是這個 repo 已經立過的規則
    （test_the_watchdog_does_not_cry_wolf）。重送多半會成功，而它成功的時候什麼事
    都沒發生過。
    """
    user = _owner(db_session)
    _log(db_session, user, _channel(db_session, user), given_up=False)

    body = client.get("/healthz", params={"deep": "1"}).json()

    assert body["checks"]["notifications"]["status"] == "ok"


def test_an_old_failure_does_not_keep_it_red_for_ever(client, db_session):
    """半個月前放棄掉的那一則，不該讓今天的探測是紅的。

    一個永遠紅著的燈會被學會忽略，然後真的停擺那一次也不會有人看。
    """
    user = _owner(db_session)
    _log(db_session, user, _channel(db_session, user), given_up=True, hours_ago=24 * 15)

    body = client.get("/healthz", params={"deep": "1"}).json()

    assert body["checks"]["notifications"]["status"] == "ok"


def test_turning_notifications_off_still_fails_the_way_it_did(client, monkeypatch, db_session):
    """本來就有的那一半不可以弄丟：功能被關掉也是「一則都不會送出」。"""
    monkeypatch.setattr(settings, "NOTIFICATIONS_ENABLED", False)

    body = client.get("/healthz", params={"deep": "1"}).json()

    assert body["checks"]["notifications"]["status"] == "fail"


def test_the_watchdog_says_what_it_means(client):
    """信裡不可以只寫「notifications 檢查失敗」。收信的人不是工程師。"""
    from scripts.watchdog import read_verdict

    problems = read_verdict(
        503,
        '{"status": "fail", "checks": {"notifications": {"status": "fail", "undelivered": 3}}}',
    )

    assert len(problems) == 1
    assert "提醒" in problems[0] or "通知" in problems[0], problems[0]
