"""一顆下架的股票，會讓他的服務每十幾分鐘被重開一次，永遠。

＊ Render 對健康檢查失敗做什麼（他們的文件，2026-09 讀的）。

    已經在跑的服務：
      失敗 15 秒 → 「Render temporarily stops routing traffic to it」
      失敗 60 秒 → 「Render automatically restarts the instance.」

    部署當中：
      15 分鐘內沒有全部通過 → 「Render cancels the deploy」

而 `render.yaml` 的 `healthCheckPath` 指的就是 `/healthz`。

＊ 於是這條路上每一個「持續存在的問題」都變成了重開機迴圈。

`/healthz` 原本的規則是「任何一格是 fail 就 503」，而那裡面有好幾格是**重開機修不好
的**，也**不會自己好**：

    一顆下架的代號       → symbols fail → 永遠 → 每十幾分鐘重開一次
    NOTIFICATIONS_ENABLED 關掉 → notifications fail → 永遠 → 一直重開
    上游整個掛掉         → 連續空輪詢 → 好幾個小時 → 一直重開
    24 小時內有一則放棄的提醒（#89）→ 那是資料庫裡的事實，重開不會變 → 連續重開一整天

重開的代價正是這個 repo 最在意的那件事：策略池被丟掉、暖身重來、那幾秒服務是斷的。
**為了保護提醒而做的探測，把提醒弄停了。**

而部署當中那條更狠：他從此收不到任何更新，包含安全修補——因為新版永遠等不到一次通
過的健康檢查。

＊ 這個判斷這個 repo 已經做過一次，只是做窄了。

`test_first_deploy_comes_up.py` 已經寫著：「a probe that never passes is a deploy
Render marks as FAILED」，所以設定模式回 200。那個推論對，範圍太小——每一個**持續存
在而且重開修不好**的狀況都是同一件事。

＊ 分法。

    /healthz          Render 在看的。503 只留給「重開這個行程有機會修好」的。
    /healthz?deep=1   看門狗／UptimeRobot 在看的。任何一格 fail 就 503。

兩邊的**內容一模一樣**——每一格的狀態都照樣在 body 裡。差的只有狀態碼，因為那兩個
讀者要拿它做的事不一樣：一個決定要不要重開這台機器，一個決定要不要寄信給人。

**預設是淺的，不是深的**，而這一點是刻意的：已經在跑的那些服務，後台的
`healthCheckPath` 是建立當下抄過去的一份（#53 的教訓），我們改 `render.yaml` 也追不
回去。所以那個沒有參數的網址必須自己就是安全的。
"""

import pytest

from app.config import settings
from app.services import worker_health


@pytest.fixture(autouse=True)
def _notifications_on(client, monkeypatch):
    """conftest 把通知關掉了，而那本身就是 deep 會紅的一格。"""
    monkeypatch.setattr(settings, "NOTIFICATIONS_ENABLED", True)


@pytest.fixture
def watching(monkeypatch):
    """一個健康的 worker，好讓每條測試只動它要動的那一格。"""

    class _Clock:
        def __init__(self) -> None:
            self.now = 0.0

        def __call__(self) -> float:
            return self.now

    clock = _Clock()
    beat = worker_health.WorkerHeartbeat(clock=clock)
    monkeypatch.setattr(settings, "WORKER_ENABLED", True)
    monkeypatch.setattr(worker_health, "heartbeat", beat)
    beat.mark_loop()
    beat.mark_poll_success()
    return beat, clock


def test_a_delisted_symbol_does_not_restart_the_instance(client, watching):
    """一顆下架的代號會永遠抓不到價，而重開機不會讓它回來。"""
    beat, clock = watching
    beat.mark_symbols(asked={"2330.TW"}, answered=set())
    clock.now += settings.HEALTH_MAX_SYMBOL_GAP_SEC + 1
    beat.mark_loop()
    beat.mark_poll_success()

    response = client.get("/healthz")

    assert response.status_code == 200, response.json()
    # 但它照樣在 body 裡——不是不說，是不要拿它去重開機器。
    assert response.json()["checks"]["symbols"]["status"] == "fail"


def test_but_the_watchdog_still_sees_it(client, watching):
    """深的那一條要紅，不然這個改動就變成「把警報關掉」。"""
    beat, clock = watching
    beat.mark_symbols(asked={"2330.TW"}, answered=set())
    clock.now += settings.HEALTH_MAX_SYMBOL_GAP_SEC + 1
    beat.mark_loop()
    beat.mark_poll_success()

    assert client.get("/healthz", params={"deep": "1"}).status_code == 503


def test_notifications_turned_off_does_not_restart_the_instance(client, monkeypatch):
    """那是一個設定，不是一個故障。重開一萬次它還是關著的。"""
    monkeypatch.setattr(settings, "NOTIFICATIONS_ENABLED", False)

    response = client.get("/healthz")

    assert response.status_code == 200, response.json()
    assert response.json()["checks"]["notifications"]["status"] == "fail"


def test_an_upstream_outage_does_not_restart_the_instance(client, watching):
    """上游整個掛掉的時候，重開我們這台不會讓 Yahoo 回來。

    而那種故障會持續好幾個小時——足夠把服務重開幾十次。
    """
    beat, clock = watching
    for _ in range(settings.HEALTH_MAX_EMPTY_POLLS):
        beat.mark_quotes_empty()

    response = client.get("/healthz")

    assert response.status_code == 200, response.json()
    assert response.json()["checks"]["market_data"]["status"] == "fail"


def test_the_two_answers_agree_on_everything_the_shallow_one_measured(client, watching):
    """兩個讀者要做的事不一樣，但看到的事實要一樣——**淺層真的量過的那幾格**。

    唯一的例外是資料庫：淺層刻意不查它，因為平台的健康檢查一直在打這個網址，每打一次
    就把免費方案的運算單元叫醒一次
    （test_the_health_probe_does_not_keep_the_database_awake 有量出來的數字）。那一格
    在淺層回的是 skipped 而不是 ok——「不知道」不可以顯示成「沒問題」。
    """
    beat, clock = watching
    beat.mark_symbols(asked={"2330.TW"}, answered=set())
    clock.now += settings.HEALTH_MAX_SYMBOL_GAP_SEC + 1
    beat.mark_loop()
    beat.mark_poll_success()

    shallow = client.get("/healthz").json()["checks"]
    deep = client.get("/healthz", params={"deep": "1"}).json()["checks"]

    assert shallow.pop("database")["status"] == "skipped"
    deep.pop("database")
    assert shallow == deep


def test_the_default_is_the_shallow_one(client, monkeypatch):
    """已經在跑的那些服務，後台的 healthCheckPath 是建立當下抄過去的一份（#53）。

    我們改 render.yaml 追不回去，所以那個**沒有參數**的網址必須自己就是安全的。
    """
    monkeypatch.setattr(settings, "NOTIFICATIONS_ENABLED", False)

    assert client.get("/healthz").status_code == 200
    assert client.get("/healthz", params={"deep": "1"}).status_code == 503


# --- 深的那一條要真的有人在問 ------------------------------------------------


def test_the_watchdog_asks_for_the_deep_one():
    """不然這個改動就只是把警報關掉。

    `HEALTH_URL` 是使用者設在自己 repo 上的變數，我們改不到——所以參數由這支腳本自己
    加上去。已經設好的人一個字都不用動。
    """
    from scripts.watchdog import deep_url

    assert deep_url("https://x.onrender.com/healthz") == "https://x.onrender.com/healthz?deep=1"


def test_it_does_not_throw_away_what_was_already_in_the_url():
    """有人可能已經在網址後面帶了東西。整串換掉會把它弄丟。"""
    from scripts.watchdog import deep_url

    assert deep_url("https://x/healthz?token=abc") == "https://x/healthz?token=abc&deep=1"


def test_asking_twice_does_not_stack_up():
    """他自己已經加過的話，不要變成 ?deep=1&deep=1。"""
    from scripts.watchdog import deep_url

    assert deep_url("https://x/healthz?deep=1") == "https://x/healthz?deep=1"


@pytest.mark.parametrize("junk", ["not a url at all", "", "://", "https://[", "ftp://x/y"])
def test_a_url_it_cannot_make_sense_of_never_stops_it(junk):
    """看門狗寧可少問一格，也不可以因為組網址而整個不跑。

    它是「沒有人看的時候唯一會說話的東西」。而網址是外面來的（repo variable 或
    argv），所以它會拿到什麼形狀我們決定不了。
    """
    from scripts.watchdog import deep_url

    assert isinstance(deep_url(junk), str)


def test_it_does_not_turn_something_unfetchable_into_something_fetchable():
    """組網址不可以順手把 scheme 補好。

    `fetch` 的第一件事就是擋掉 http/https 以外的 scheme（`file:///etc/passwd` 會讀本
    機檔案），而那個擋法只有在這裡沒有自作聰明的時候才成立。
    """
    import urllib.parse

    from scripts.watchdog import deep_url

    for junk in ["not a url at all", "ftp://x/y"]:
        assert urllib.parse.urlparse(deep_url(junk)).scheme == urllib.parse.urlparse(junk).scheme


# --- 兩邊都要指對地方，而指錯不會有任何東西變紅 ------------------------------


def test_the_platform_health_check_is_not_pointed_at_the_deep_one():
    """把 `healthCheckPath` 改成深的那一條，就是把重開機迴圈裝回去。

    而那個改動看起來完全合理（「健康檢查當然要檢查全部啊」），做下去也不會有任何東西
    變紅——症狀是使用者那邊每十幾分鐘斷一次線，加上再也收不到更新。
    """
    from pathlib import Path

    render_yaml = Path(__file__).resolve().parents[2] / "render.yaml"
    lines = [
        line
        for line in render_yaml.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]
    path_lines = [line for line in lines if "healthCheckPath" in line]

    assert path_lines, "render.yaml 裡找不到 healthCheckPath"
    for line in path_lines:
        assert "deep" not in line, line


def test_the_watchdog_actually_uses_it():
    """`deep_url` 寫好了但沒人呼叫，等於這整個改動只是把警報關掉。"""
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "scripts" / "watchdog.py"
    body = source.read_text(encoding="utf-8")
    after_def = body.split("def main(", 1)

    assert len(after_def) == 2, "watchdog.py 沒有 main()"
    assert "deep_url(" in after_def[1], "main() 沒有把網址換成深的那一條"


# --- 「重開有機會修好」比第一版想的還要窄 ------------------------------------


def test_a_database_that_is_gone_does_not_restart_the_instance(client, monkeypatch):
    """第一版把資料庫算成「重開有機會修好」，那是錯的。

    ＊ 為什麼一開始會那樣想。

    連線池壞掉重開確實會重建。但那種故障是**幾秒**的事，根本撐不到六十秒的門檻——真
    的會撐過門檻的，是連線字串打錯、Neon 專案被刪掉、或**免費方案的每月運算時數用完
    了**。

    最後那一個是這條路上會**每個月固定發生一次**的：Neon 免費方案給 100 CU-hours，
    以 0.25 CU 算是 400 小時，而這個 app 每五分鐘就碰一次資料庫（關市時也是），所以
    運算單元幾乎不會休眠——一個月 730 小時，大約第十七天就用完，然後「compute is
    suspended until the next billing period」。

    也就是說：每個月後半，他的資料庫是關的。而如果那也算「重開有機會修好」，那半個
    月裡他的服務會每分鐘被重開一次——重開一萬次，Neon 的帳單週期還是不會提前到。
    """
    monkeypatch.setattr("app.api.routers.health._check_database", lambda db: {"status": "fail"})

    response = client.get("/healthz")

    assert response.status_code == 200, response.json()
    # 淺層連問都不問了（那一次查詢本身就是額度成本），所以事實在深的那一條上。
    deep = client.get("/healthz", params={"deep": "1"})
    assert deep.status_code == 503
    assert deep.json()["checks"]["database"]["status"] == "fail"


def test_a_poll_that_never_succeeds_does_not_restart_the_instance(client, watching):
    """輪詢做不完的原因幾乎都在外面：上游、資料庫、網路。

    迴圈自己還在轉（那一格是分開的），所以重開這個行程換不到任何東西。
    """
    beat, clock = watching
    clock.now += settings.HEALTH_MAX_AGE_SEC + 1
    # 迴圈照轉，只有輪詢做不完。
    beat.mark_loop()

    response = client.get("/healthz")

    assert response.status_code == 200, response.json()
    assert response.json()["checks"]["market_data"]["status"] == "fail"


def test_the_only_thing_left_is_a_loop_that_stopped_turning(client, watching):
    """而這一格要留著，因為它正是重開機唯一真的修得好的那一種：

    行程還活著、還在回應 HTTP，但那條 while 迴圈不再往前——`tick_once` 卡在一個永遠
    不會回答的 socket 上。除了重開沒有別的辦法。
    """
    beat, clock = watching
    clock.now += settings.HEALTH_MAX_AGE_SEC + 1

    assert client.get("/healthz").status_code == 503
