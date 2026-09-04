"""健康檢查每被打一次就敲一次資料庫，而打它的東西比資料庫的休眠門檻密得多。

＊ 量出來的。

維護者的 Neon 主控台，2026-09-04：**24.12 / 100 CU-hrs，用量從 9/1 起算**。三天多用掉
四分之一，照這個速度 9 月 15 號前後就會用完，然後

    compute is suspended until the next billing period

剩下的半個月，一則提醒都不會送出。

再換算一次更清楚：24.12 ÷ 0.25 CU = **96.5 個運算小時**，而那段期間只有 82 個牆上小
時。它不只是一直醒著，還偶爾往上擴。

＊ 為什麼把輪詢放慢還不夠。

`CLOSED_POLL_INTERVAL_SEC` 從 5 分鐘拉到 30 分鐘，是為了讓那顆運算單元睡得著（Neon 閒
置五分鐘才休眠，而且免費方案關不掉）。那個推論漏了一件事：**盯盤迴圈不是唯一在敲資料
庫的東西。**

`/healthz` 每次被打都會 `SELECT 1`，而打它的有：

    平台自己的健康檢查    一直在打，那正是它的工作
    前端只要開著          每 30 秒一次（WorkerHealthBanner）

任何一個都比五分鐘密。所以就算迴圈半小時才碰一次資料庫，那顆運算單元照樣不會睡——省
下來的部分被健康檢查抵銷掉了。

＊ 修法：淺的那一條不需要問資料庫。

#94 之後，沒帶參數的 `/healthz` 唯一會 503 的條件是「盯盤迴圈不再往前」——**那一格跟資
料庫沒有關係**。所以淺層不查，`?deep=1` 才查。

而深的那一條是給看門狗和監控服務的，它們每 5 分鐘打一次——那個頻率剛好踩在休眠門檻
上。這是刻意接受的：那條路是「有沒有人會被通知」唯一的外部觀測，而它換來的是每 5 分
鐘一次而不是每 30 秒一次。

＊ 誠實優先於好看。

淺層不查，就不可以在 body 裡把 `database` 寫成 `ok`——那是這個 repo 一路在守的同一條
規則的第五次出現：**「不知道」不可以顯示成「沒問題」。**
"""

import pytest
from sqlalchemy import event

from app.config import settings
from app.services import worker_health


@pytest.fixture
def counted(db_session):
    """數這個請求真的送出去幾句 SQL。

    不用 monkeypatch `_check_database`：那只證明那個函式沒被呼叫，不證明「這條路真的
    沒有碰資料庫」——而後者才是 Neon 在計費的東西。連線池的 pre-ping 也算，所以要在引
    擎那一層數。
    """
    engine = db_session.get_bind()
    seen: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(engine, "after_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(engine, "after_cursor_execute", record)


def test_the_probe_the_platform_polls_never_touches_the_database(client, counted):
    """平台的健康檢查一直在打這個網址。它每打一次就把資料庫叫醒一次的話，那顆運算單
    元永遠睡不著——而免費方案的額度是照醒著的時間算的。
    """
    counted.clear()

    client.get("/healthz")

    assert counted == [], f"淺層探測送出了 {len(counted)} 句 SQL：{counted}"


def test_the_deep_one_still_checks_it(client, counted):
    """看門狗要問的是「有沒有人會被通知」，而資料庫連不上的時候答案是沒有。

    它每 5 分鐘打一次，那個頻率是刻意接受的代價。
    """
    counted.clear()

    client.get("/healthz", params={"deep": "1"})

    assert counted, "深層探測沒有去問資料庫"


def test_the_shallow_body_does_not_claim_the_database_is_fine(client):
    """**「不知道」不可以顯示成「沒問題」。**

    這是這條規則在這個 repo 裡的第五次出現（build_info、update_check、系統狀態頁、右
    下角那一格，現在是這裡），因為每一次被違反的後果都一樣：有人照著一個沒有根據的綠
    燈做決定。
    """
    body = client.get("/healthz").json()

    assert body["checks"]["database"]["status"] != "ok"


def test_the_shallow_body_says_where_to_get_the_real_answer(client):
    """讀 body 的人要知道那一格為什麼是空的，以及去哪裡問得到。"""
    detail = str(client.get("/healthz").json()["checks"]["database"])

    assert "deep" in detail


def test_a_database_that_is_gone_does_not_make_the_platform_restart_it(client, monkeypatch):
    """淺層不查資料庫，所以資料庫不見了也不會讓平台每分鐘重開這個行程一次。

    這一條在 #94 就決定了（重開一萬次，帳單週期也不會提前），現在連問都不問了。
    """
    monkeypatch.setattr("app.api.routers.health._check_database", lambda db: {"status": "fail"})

    assert client.get("/healthz").status_code == 200


def test_the_worker_check_still_works_without_the_database(client, monkeypatch):
    """拿掉資料庫那一格，不可以順手把這條路上唯一還會 503 的東西也拿掉。"""
    monkeypatch.setattr(settings, "WORKER_ENABLED", True)
    clock = _Clock()
    beat = worker_health.WorkerHeartbeat(clock=clock)
    monkeypatch.setattr(worker_health, "heartbeat", beat)
    beat.mark_loop()
    beat.mark_poll_success()
    clock.now += settings.HEALTH_MAX_AGE_SEC + 1

    assert client.get("/healthz").status_code == 503


def test_the_version_is_still_on_the_shallow_answer(client):
    """CI 的部署確認靠這一格判斷「線上跑的是不是剛推的那個 commit」。

    它讀的是同一個沒有參數的網址，而那一格是從環境變數來的，不需要資料庫。
    """
    assert "version" in client.get("/healthz").json()


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now
