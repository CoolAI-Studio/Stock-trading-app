"""抓不到 K 棒 = 那幾支策略停擺，而現在沒有任何地方看得見。

行情那一半已經看得見了（心跳、連續空輪、逐代號缺價），策略子行程那一半也是（#18 之
後）。中間漏掉的是第三種：**報價抓得到，但 K 棒抓不到。**

那不是假設的組合——報價和 K 棒走的是上游不同的端點。K 棒那條掛掉的時候：

    consecutive_empty_polls   0（報價回來了）
    symbol_gap_sec            空的（每個代號都有價）
    strategy_blocked_sec      空的（子行程好好的）
    /healthz                  全綠
    外部看門狗                永遠不會寄信

而每一支 on_bar 策略一則提醒都沒發出。這正是 test_a_dead_strategy_worker_is_visible
的檔頭講的那件事，只是換了一個成因：`_record_feed_problem` 刻意不累積、不停用（抓不
到資料不是使用者的錯，那條規則要留著），但它同時也不進任何計數器。

而且這條路上的人變多了：空清單現在會被擋在抓取端、轉成一次「抓不到 K 棒」，所以原本
會顯示成「還在暖身」或直接 IndexError 的那些，現在也走這裡。

門檻沿用 HEALTH_MAX_SYMBOL_GAP_SEC，不另外開一格：問的是同一件事——「這個代號多久沒
有拿到資料了」。多一個旋鈕就多一格空白，而部署表單上的空白是這個專案數得出來的成本。

半夜不要亂叫也一起守著（test_the_watchdog_does_not_cry_wolf.py）：這一輪沒有被問到的
序列不算壞掉。
"""

from decimal import Decimal

import pytest

from app.config import settings
from app.enums import DataSource
from app.models.strategy import Strategy
from app.models.user import User
from app.services import market_loop, worker_health
from app.services.market_data.base import Bar, BarFetchError, Quote, Timeframe
from app.services.market_data.service import MarketDataService
from app.services.worker_health import WorkerHeartbeat

BAR_SOURCE = (
    "class Strategy:\n"
    "    def __init__(self):\n"
    "        self.name = 'weekly'\n"
    "        self.symbol = 'AAPL'\n"
    "        self.timeframe = '1wk'\n"
    "        self.warmup_bars = 0\n"
    "    def on_bar(self, bar):\n"
    "        return 'HOLD'\n"
)


class _FakeClock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# --- 記帳 -------------------------------------------------------------------


def test_a_series_whose_bars_cannot_be_fetched_accumulates_a_gap():
    beat = WorkerHeartbeat(clock=_FakeClock())

    beat.mark_bar_gaps({"AAPL 1wk"})

    assert set(beat.snapshot().bar_gap_sec) == {"AAPL 1wk"}


def test_the_gap_starts_when_it_broke_not_when_it_was_last_asked():
    """持續中的故障要留著它**開始**的時間。

    每一輪重新計時的話，任何門檻都永遠跨不過去——五秒一輪，而門檻是十五分鐘。
    """
    clock = _FakeClock()
    beat = WorkerHeartbeat(clock=clock)

    beat.mark_bar_gaps({"AAPL 1wk"})
    clock.advance(600.0)
    beat.mark_bar_gaps({"AAPL 1wk"})

    assert beat.snapshot().bar_gap_sec["AAPL 1wk"] == pytest.approx(600.0)


def test_one_good_fetch_clears_it():
    beat = WorkerHeartbeat(clock=_FakeClock())
    beat.mark_bar_gaps({"AAPL 1wk"})

    beat.mark_bar_gaps(set())

    assert beat.snapshot().bar_gap_sec == {}


def test_a_series_nobody_asked_this_round_is_not_reported_as_broken():
    """「沒有被問」不等於「壞掉」。

    使用者把那支策略停掉、或者改成別的週期之後，那一格要自己消失——不然刪掉壞掉的那
    一列就不再是有效的修法，而一個修不掉的紅燈會被學會忽略。
    """
    beat = WorkerHeartbeat(clock=_FakeClock())
    beat.mark_bar_gaps({"AAPL 1wk", "2330.TW 1d"})

    beat.mark_bar_gaps({"2330.TW 1d"})

    assert set(beat.snapshot().bar_gap_sec) == {"2330.TW 1d"}


# --- 迴圈真的會記 -----------------------------------------------------------


class _DeadBarFeed:
    """報價回得來、K 棒回不來。這正是看不見的那個組合。"""

    data_source = DataSource.YFINANCE

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        return {
            s: Quote(symbol=s, data_source=self.data_source, price=Decimal(100)) for s in symbols
        }

    def get_bars(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Bar]:
        raise BarFetchError("上游擋住了")


@pytest.fixture
def fake_heartbeat(monkeypatch):
    clock = _FakeClock()
    beat = WorkerHeartbeat(clock=clock)
    monkeypatch.setattr(worker_health, "heartbeat", beat)
    return beat, clock


def _a_bar_strategy(db_session, *, email="deadbars@example.com", symbol="AAPL"):
    user = User(email=email, hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    strategy = Strategy(
        user_id=user.id,
        name="週線策略",
        symbol=symbol,
        source_code=BAR_SOURCE,
        code_hash=f"irrelevant-{email}",
        default_quantity=Decimal(1),
        is_active=True,
    )
    db_session.add(strategy)
    db_session.commit()
    db_session.refresh(strategy)
    return user, strategy


def _service() -> MarketDataService:
    return MarketDataService(providers={DataSource.YFINANCE: _DeadBarFeed()})


def test_the_loop_records_a_series_it_could_not_fetch(db_session, fake_heartbeat):
    beat, _ = fake_heartbeat
    _a_bar_strategy(db_session)

    market_loop.tick_once(db=db_session, market_data_service=_service())

    assert set(beat.snapshot().bar_gap_sec) == {"AAPL 1wk"}


def test_and_the_old_rule_still_holds(db_session, fake_heartbeat):
    """看得見**不等於**開始怪罪使用者。

    抓不到資料還是不累積、不停用。這一條放在這裡，是因為「讓它看得見」最容易的錯誤修
    法就是把它改回 _record_strategy_error。
    """
    _, strategy = _a_bar_strategy(db_session)
    service = _service()

    for _ in range(6):
        market_loop.tick_once(db=db_session, market_data_service=service)

    db_session.refresh(strategy)
    assert strategy.is_active, "上游抓不到 K 棒把使用者的策略停用了"
    assert strategy.consecutive_errors == 0


# --- 公開探測 ---------------------------------------------------------------


def _healthz(client, monkeypatch, *, gap_for: float, gaps=frozenset({"AAPL 1wk"})):
    clock = _FakeClock()
    beat = WorkerHeartbeat(clock=clock)
    beat.mark_bar_gaps(set(gaps))
    clock.advance(gap_for)
    beat.mark_loop()
    beat.mark_poll_success()
    beat.mark_quotes_fetched()
    monkeypatch.setattr(settings, "WORKER_ENABLED", True)
    monkeypatch.setattr(settings, "NOTIFICATIONS_ENABLED", True)
    monkeypatch.setattr(worker_health, "heartbeat", beat)
    # **看門狗看的是深的那一條。** `/healthz` 沒帶參數的時候只回答「重開這台機器有沒有
    # 機會修好」——Render 的健康檢查看的是它，而它失敗 60 秒就會把行程重開（見
    # test_the_probe_render_watches_cannot_restart_him_forever）。這裡問的是「有沒有人
    # 會被通知」，那是 ?deep=1。
    return client.get("/healthz", params={"deep": "1"})


def test_a_brief_bar_outage_does_not_page_anyone(client, monkeypatch):
    """上游擋你一下下，下一輪就好了。那不是停擺。"""
    assert _healthz(client, monkeypatch, gap_for=30.0).status_code == 200


def test_a_bar_feed_that_has_been_down_for_a_long_time_turns_the_probe_red(client, monkeypatch):
    resp = _healthz(client, monkeypatch, gap_for=settings.HEALTH_MAX_SYMBOL_GAP_SEC + 1)

    assert resp.status_code == 503
    assert resp.json()["checks"]["bars"]["status"] == "fail"


def test_the_probe_counts_them_but_does_not_name_them(client, monkeypatch):
    """/healthz 沒有憑證也打得到，所以它只說「幾段」。是哪一段要登入才看得到——跟代
    號和策略那兩格同一條規則。"""
    resp = _healthz(
        client,
        monkeypatch,
        gap_for=settings.HEALTH_MAX_SYMBOL_GAP_SEC + 1,
        gaps=frozenset({"SECRETCO 1d", "PRIVATE 1wk"}),
    )

    assert resp.json()["checks"]["bars"]["stale_count"] == 2
    assert "SECRETCO" not in resp.text


def test_the_check_is_disabled_along_with_the_worker(client, monkeypatch):
    monkeypatch.setattr(settings, "WORKER_ENABLED", False)

    assert client.get("/healthz").json()["checks"]["bars"]["status"] == "disabled"


# --- 狀態頁：是哪一段 --------------------------------------------------------


def _stuck_deployment(monkeypatch, gaps: set[str]):
    clock = _FakeClock()
    beat = WorkerHeartbeat(clock=clock)
    beat.mark_bar_gaps(gaps)
    clock.advance(settings.HEALTH_MAX_SYMBOL_GAP_SEC + 1)
    beat.mark_loop()
    beat.mark_poll_success()
    monkeypatch.setattr(settings, "WORKER_ENABLED", True)
    monkeypatch.setattr(settings, "NOTIFICATIONS_ENABLED", True)
    monkeypatch.setattr(worker_health, "heartbeat", beat)


def test_the_owner_can_see_which_series_is_stuck(auth_client, db_session, monkeypatch):
    owner = db_session.query(User).filter(User.email == "fixture-user@example.com").one()
    db_session.add(
        Strategy(
            user_id=owner.id,
            name="我的週線策略",
            symbol="AAPL",
            source_code=BAR_SOURCE,
            code_hash="hash-bar-status-page",
            default_quantity=Decimal(1),
        )
    )
    db_session.commit()
    _stuck_deployment(monkeypatch, {"AAPL 1wk"})

    body = auth_client.get("/api/system/status").json()

    stale = body["market_data"]["stale_bars"]
    assert [row["series"] for row in stale] == ["AAPL 1wk"]
    assert body["overall"] == "fail"


def test_somebody_elses_stuck_feed_is_not_your_business(auth_client, db_session, monkeypatch):
    """心跳是行程層級的單例，它的表是跨全部帳號的聯集。

    別人在盯什麼代號不是你的事，而且那是一個你按不動的紅燈。
    """
    _a_bar_strategy(db_session, email="stranger@example.com", symbol="SECRETCO")
    _stuck_deployment(monkeypatch, {"SECRETCO 1wk"})

    response = auth_client.get("/api/system/status")
    body = response.json()

    assert body["market_data"]["stale_bars"] == []
    assert "SECRETCO" not in response.text
    assert body["overall"] != "fail"


# --- 那封信 ------------------------------------------------------------------


def test_the_watchdog_says_what_a_dead_bar_feed_means():
    """信裡不可以只寫「bars 檢查失敗」。收信的人不是工程師。"""
    from scripts.watchdog import read_verdict

    problems = read_verdict(
        503,
        '{"status": "fail", "checks": {"bars": {"status": "fail", "stale_count": 2}}}',
    )

    assert len(problems) == 1
    assert "K 棒" in problems[0], problems[0]
