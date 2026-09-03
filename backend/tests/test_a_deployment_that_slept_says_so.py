"""休眠期間一則提醒都沒送出，而醒來之後每一個探測都是綠的。

＊ 這個洞的形狀。

Render 的免費方案，**沒有外來流量 15 分鐘就休眠**。休眠 = 行程結束 = 盯盤迴圈停
掉 = 那段時間裡穿價、跌破均線、觸發停損，一則提醒都不會送出。

而醒來之後：

    他打開 app（這個動作本身就把服務叫醒了）
    → 行程剛起來，心跳是新的
    → /healthz 綠、/system 綠、看門狗一封信都沒寄
    → 畫面上沒有任何一個地方說「你剛剛有八個小時沒有在盯盤」

看門狗自己也看不到：它去打 /healthz 的那一下**就是**把服務叫醒的那一下，所以它
永遠只看得到一個剛起床、精神很好的行程。

這跟 #18（子行程全面停擺）、#67（K 棒抓不到）是同一個形狀，只是這次連迴圈本身都
不在了——所以行程內的心跳一個字都說不出來，它是跟著行程一起死掉的那個東西。

＊ 唯一還記得的東西在資料庫裡。

`market_quotes.fetched_at` 是每一輪輪詢都會寫的牆上時鐘。而**關市不會讓它停**：
迴圈在關市時把週期從 5 秒拉長到 300 秒（`CLOSED_POLL_INTERVAL_SEC`），但照樣抓。
所以「最後一次抓到報價是 8 小時前」的意思只有一個：**這 8 小時裡沒有行程在跑。**

（這一點是這整個判斷成立的前提。如果關市時完全不抓，那每天早上開機都會看起來像
睡了 17 個小時，而這個功能就會變成一個每天喊一次狼來了的東西。）

＊ 為什麼不讓 /healthz 變紅。

那個洞已經過去了——現在是醒著的。紅燈是給「現在停擺了」用的，而看門狗每 15 分鐘
打一次，一次醒來就寄一封「你剛剛睡著了」的信，收件匣會被塞爆到他不再看。說一次、
說在他真的會看的那一頁上，然後停在那裡。
"""

from datetime import timedelta

import pytest

from app.config import settings
from app.enums import DataSource
from app.models.market import MarketQuote
from app.models.mixins import utcnow
from app.models.strategy import Strategy
from app.models.user import User
from app.services import market_loop, worker_health

TICK_SOURCE = (
    "class Strategy:\n"
    "    def __init__(self):\n"
    "        self.name = 's'\n"
    "    def on_tick(self, price):\n"
    "        return 'HOLD'\n"
)


@pytest.fixture(autouse=True)
def _fresh_heartbeat():
    """心跳是模組層的單例，一條測試留下的痕跡會變成下一條的假綠燈。"""
    before = worker_health.heartbeat
    worker_health.heartbeat = worker_health.WorkerHeartbeat()
    yield
    worker_health.heartbeat = before


def _watching(db_session) -> Strategy:
    user = User(email="slept@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    strategy = Strategy(
        user_id=user.id,
        name="盯著",
        symbol="2330.TW",
        data_source=DataSource.YFINANCE,
        source_code=TICK_SOURCE,
        code_hash="irrelevant-for-tests",
        is_active=True,
    )
    db_session.add(strategy)
    db_session.commit()
    return strategy


def _last_fetched(db_session, *, minutes_ago: float) -> None:
    db_session.add(
        MarketQuote(
            symbol="2330.TW",
            data_source=DataSource.YFINANCE,
            price=900,
            fetched_at=utcnow() - timedelta(minutes=minutes_ago),
        )
    )
    db_session.commit()


def test_a_gap_of_hours_is_reported(db_session):
    """八小時沒有行程在跑，那八小時裡的每一次穿價都沒有人在看。"""
    _watching(db_session)
    _last_fetched(db_session, minutes_ago=8 * 60)

    slept = market_loop.note_downtime_since_last_run(db_session)

    assert slept is not None
    assert 7.9 * 3600 < slept < 8.1 * 3600


def test_it_survives_into_the_snapshot(db_session):
    """只在開機那一刻算得出來，所以要記住——下一輪輪詢就把 fetched_at 蓋掉了。"""
    _watching(db_session)
    _last_fetched(db_session, minutes_ago=8 * 60)

    market_loop.note_downtime_since_last_run(db_session)

    assert worker_health.heartbeat.snapshot().slept_sec is not None


def test_a_normal_redeploy_is_not_an_outage(db_session):
    """每一次更新都會重啟行程。那也是一段空白，但它是**我們**造成的、幾分鐘、而且
    每一次部署都會發生——把它算進去，這個訊號會在第一天就被學會忽略。"""
    _watching(db_session)
    _last_fetched(db_session, minutes_ago=3)

    assert market_loop.note_downtime_since_last_run(db_session) is None


def test_a_brand_new_deployment_has_not_missed_anything(db_session):
    """一筆報價都沒有＝從來沒跑過，不是睡了很久。第一次部署的人不該看到這句話。"""
    _watching(db_session)

    assert market_loop.note_downtime_since_last_run(db_session) is None


def test_watching_nothing_means_there_was_nothing_to_miss(db_session):
    """他把策略全部停掉，報價就不再更新了。那不是停擺，那是沒事做。

    少了這一條，一個空的部署每次重啟都會說自己睡了很久——而那句話是假的。
    """
    _last_fetched(db_session, minutes_ago=8 * 60)

    assert market_loop.note_downtime_since_last_run(db_session) is None


def test_it_never_takes_the_worker_down_with_it(db_session, monkeypatch):
    """這句話是在盯盤迴圈起跑之前算的。它拋出去的話，整個迴圈就不會開始跑——
    為了一句「你剛剛睡著了」而讓他真的睡著，剛好把事情做反。"""

    def boom(*args, **kwargs):
        raise RuntimeError("資料庫這一刻不在")

    monkeypatch.setattr(market_loop, "_watched_symbols", boom)

    assert market_loop.note_downtime_since_last_run(db_session) is None


def test_the_page_he_opens_says_it(auth_client, db_session):
    """隔著管線問：他打開的是那一頁，不是這個函式。"""
    _watching(db_session)
    _last_fetched(db_session, minutes_ago=8 * 60)
    market_loop.note_downtime_since_last_run(db_session)

    worker = auth_client.get("/api/system/status").json()["worker"]

    assert worker["slept_sec"] is not None
    assert worker["slept_sec"] > 7 * 3600


def test_the_watchdog_is_not_woken_by_a_gap_that_is_over(client, db_session, monkeypatch):
    """那個洞已經過去了，現在是醒著的。

    看門狗每 15 分鐘打一次，而免費方案本來就會反覆休眠——每次醒來寄一封信，
    收件匣一天就會被塞到他不再看，然後真的停擺那次也不會有人看。
    """
    _watching(db_session)
    _last_fetched(db_session, minutes_ago=8 * 60)
    market_loop.note_downtime_since_last_run(db_session)
    # conftest 的 client 會把通知關掉，而那本來就會讓 /healthz 是紅的——不設回來的
    # 話這條測試會為了完全不相干的理由通過，等於什麼都沒測。
    monkeypatch.setattr(settings, "NOTIFICATIONS_ENABLED", True)

    response = client.get("/healthz")

    assert response.status_code == 200, response.json()
    assert response.json()["checks"]["worker"]["status"] != "fail"
