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

from app.config import settings
from app.services import worker_health
from app.services.market_loop import CLOSED_POLL_INTERVAL_SEC


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


def _a_loop_that_just_finished_a_poll(monkeypatch) -> "_Clock":
    """一個剛剛成功跑完一輪的盯盤迴圈。"""
    monkeypatch.setattr(settings, "WORKER_ENABLED", True)
    clock = _Clock()
    beat = worker_health.WorkerHeartbeat(clock=clock)
    monkeypatch.setattr(worker_health, "heartbeat", beat)
    beat.mark_loop()
    beat.mark_poll_success()
    beat.expect_next_within(CLOSED_POLL_INTERVAL_SEC)
    return clock


def test_the_deep_one_reuses_the_work_the_loop_already_did(client, counted, monkeypatch):
    """迴圈剛跑完一輪 = 資料庫剛剛被用過而且能用。不要再問一次。

    ＊ 為什麼要擋，明明深層一天只被打 288 次。

    因為每一次都是**一次喚醒**。免費方案閒置 5 分鐘才休眠，所以每 5 分鐘問一次等於那
    顆運算單元永遠醒著——一個月 730 小時，而額度是 400 小時。

    而那個 5 分鐘的監控是**我們自己的文件教出來的**（DEPLOYMENT.md、docs/install.html
    在 2026-09-04 之前都寫著 `?deep=1` ＋ 5 分鐘）。照做的人不會回來重讀文件，我們也搆
    不到他的 UptimeRobot——所以只有端點自己擋得住。這跟資料庫預設換不換是同一條教訓：
    **改設定只幫得到還沒裝的人，改程式才救得到已經在跑的那些。**

    ＊ 為什麼這個答案比 SELECT 1 好。

    `mark_poll_success()` 只在 `tick_once` 整輪沒炸的時候才會被呼叫，而那一輪從頭到尾
    都在讀寫資料庫。所以它證明的是**真正要用的那條路通**，不是一句人造的查詢通。
    """
    clock = _a_loop_that_just_finished_a_poll(monkeypatch)
    clock.now += 60  # 一分鐘前剛跑完
    counted.clear()

    response = client.get("/healthz", params={"deep": "1"})

    # 不問整體狀態碼：測試環境裡 NOTIFICATIONS_ENABLED 是關的，那一格本來就紅。
    assert response.json()["checks"]["database"]["status"] == "ok"
    assert counted == [], f"迴圈剛證明過了，深層探測還是自己又問了一次：{counted}"


def test_it_asks_for_itself_once_the_loops_evidence_has_expired(client, counted, monkeypatch):
    """證據會過期。過期之後不可以繼續拿它當答案——那就變成猜的。

    門檻跟 worker 那一格用的是同一個（`_max_age`），也就是迴圈自己說的下一輪期限。過
    了那個時間迴圈還沒回報成功，資料庫能不能用就是一件沒有人知道的事了。
    """
    clock = _a_loop_that_just_finished_a_poll(monkeypatch)
    clock.now += CLOSED_POLL_INTERVAL_SEC + settings.HEALTH_TICK_BUDGET_SEC + 1
    counted.clear()

    client.get("/healthz", params={"deep": "1"})

    assert counted, "迴圈的證據已經過期，深層探測卻沒有自己去問資料庫"


def test_a_database_that_is_gone_still_turns_the_deep_probe_red(client, monkeypatch):
    """省下來的喚醒不可以連「資料庫掛了」都一起省掉。

    迴圈碰不到資料庫就不會再 `mark_poll_success`，所以那份證據一定會過期；過期之後這
    條路自己去問，於是照樣紅。看門狗寄不寄得出信，靠的就是這一格。
    """
    clock = _a_loop_that_just_finished_a_poll(monkeypatch)
    clock.now += CLOSED_POLL_INTERVAL_SEC + settings.HEALTH_TICK_BUDGET_SEC + 1
    monkeypatch.setattr("app.api.routers.health._check_database", lambda db: {"status": "fail"})

    response = client.get("/healthz", params={"deep": "1"})

    assert response.status_code == 503
    assert response.json()["checks"]["database"]["status"] == "fail"


def test_the_shallow_probe_sends_no_sql_with_notifications_switched_on(
    client, counted, monkeypatch
):
    """上面那條測試缺的一半，而缺的那一半正是線上真正在跑的設定。

    ＊ 這條測試存在的原因。

    `client` fixture 會把 `NOTIFICATIONS_ENABLED` 關掉（測試不該真的送出通知），而
    `checks["notifications"]` 那一格**只有在它開著的時候才會去查資料庫**——一句
    `SELECT count(...)` 數最近放棄掉的提醒。

    於是 `test_the_probe_the_platform_polls_never_touches_the_database` 一直是綠的，而
    線上每一次健康檢查都在敲資料庫。量出來的（2026-09-05 21:52–22:07，收盤後、盯盤迴
    圈從 35 秒一路睡到 956 秒）：

        每分鐘 15.4 句 SQL，16 次取樣裡「上次 SQL」沒有一次超過 5 秒

    平台自己的健康檢查每幾秒就打一次 `/healthz`，所以那一格等於把免費方案的運算單元釘
    在醒著的狀態——這就是 #98、#99 兩輪都沒讓用量掉下來的原因：拿掉了 `SELECT 1`，卻留
    著它正下方的 `SELECT count(...)`。

    ＊ 教訓寫在這裡，因為它會再犯。

    **fixture 關掉的東西，就是測試看不到的東西。** 一格只在某個設定下才走的路，要在那
    個設定下測，否則綠燈證明的是另一份程式。
    """
    monkeypatch.setattr(settings, "NOTIFICATIONS_ENABLED", True)
    counted.clear()

    client.get("/healthz")

    assert counted == [], f"淺層探測在通知開著的時候送出了 {len(counted)} 句 SQL：{counted}"


def test_the_watchdog_still_sees_alerts_that_gave_up_without_asking_the_database(
    client, counted, monkeypatch
):
    """省下來的喚醒，不可以把「提醒送不出去」這一格一起省掉。

    那一格是看門狗唯一看得到「他的 bot token 被撤銷、每一則都失敗、重送用完」的地方，
    而那正是這個產品最不能發生的事。所以搬家之後它要**照樣會紅**——只是數字改由盯盤迴
    圈在它自己那一輪裡數好（那一輪本來就在用資料庫），探測只讀記憶體。
    """
    monkeypatch.setattr(settings, "NOTIFICATIONS_ENABLED", True)
    clock = _a_loop_that_just_finished_a_poll(monkeypatch)
    worker_health.heartbeat.mark_undelivered(3)
    clock.now += 60
    counted.clear()

    response = client.get("/healthz", params={"deep": "1"})

    assert counted == [], f"讀迴圈數好的那個數字時又查了一次資料庫：{counted}"
    assert response.status_code == 503
    assert response.json()["checks"]["notifications"] == {"status": "fail", "undelivered": 3}
