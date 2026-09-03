"""策略子行程全部起不來 = 提醒全面停擺，而現在沒有任何地方看得見。

#18 把使用者的策略搬進固定三個子行程，並且立了一條對的規則：子行程壞掉不是策略的
錯，所以走 `_record_feed_problem`——不累積、不停用。那條規則要留著。

但它同時也**不發事件、不寫 log、不進任何計數器**。於是「三個 worker 都起不來」這
個狀態長這樣：

    /healthz          四項檢查全綠（迴圈在轉、行情抓得到、代號都有價、通知開著）
    /system           沒有任何一格在講策略子行程
    外部看門狗        永遠不會寄信，因為 /healthz 回 200
    使用者看得到的    每一列策略上一句 last_error，而他不會去開那一頁

CLAUDE.md 的可觀測性那一列寫的是「警告不能停擺，就必須看得到它有沒有在跑」。行情
那一半已經做到了（心跳、連續空輪、逐代號缺價），策略那一半沒有——而策略正是這個
產品拿來發出提醒的東西。子行程起不來的時候，行情每五秒抓得好好的，一則提醒都不會
發出，而每一個探測都說正常。

WHY THIS IS NOT 「多一個指標」。這個部署上沒有人在看 log，也沒有 Grafana（刻意
的，見 system.py 的檔頭）。唯一會在沒有人看的時候主動說話的東西是外部看門狗，而它
只讀 /healthz。所以「看得見」在這個專案裡的定義就是：/healthz 會紅、看門狗的信說
得出人話、狀態頁指得出是哪一支。

WHY IT IS ALLOWED TO GO RED AND STAY RED，跟 test_healthz_sees_a_dead_symbol.py
同一個理由：它是**可行動**的（重新部署一次），而且會自己好——子行程一旦起得來，
下一輪就清掉了。

半夜不要亂叫（test_the_watchdog_does_not_cry_wolf.py）也一起守著：這一輪沒有被問
到的策略不算壞掉。關市的時候 on_tick 策略根本不會被呼叫，把「沒有被問」記成「壞
掉」的話，台股使用者每天晚上都會收到一封信。
"""

from decimal import Decimal

import pytest

from app.config import settings
from app.enums import DataSource
from app.models.strategy import Strategy
from app.models.user import User
from app.services import market_loop, worker_health
from app.services.market_data.providers.mock_provider import MockProvider
from app.services.market_data.service import MarketDataService
from app.services.strategy_worker import WorkerUnavailable
from app.services.worker_health import WorkerHeartbeat


class _FakeClock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# --- 記帳 -------------------------------------------------------------------


def test_a_strategy_whose_worker_will_not_start_accumulates_a_gap():
    clock = _FakeClock()
    beat = WorkerHeartbeat(clock=clock)

    beat.mark_blocked_strategies({7})
    clock.advance(600.0)
    beat.mark_blocked_strategies({7})

    assert beat.snapshot().strategy_blocked_sec == {7: 600.0}


def test_the_gap_starts_when_it_broke_not_when_the_process_booted():
    """早上都好好的、中午死掉的策略，是從中午開始瞎的。

    每一輪重新計時的話，任何門檻都永遠跨不過去，這個警報器就等於不存在。
    """
    clock = _FakeClock()
    beat = WorkerHeartbeat(clock=clock)
    beat.mark_blocked_strategies(set())

    clock.advance(1_000.0)
    beat.mark_blocked_strategies({7})
    clock.advance(300.0)

    assert beat.snapshot().strategy_blocked_sec == {7: 300.0}


def test_one_good_run_clears_it():
    clock = _FakeClock()
    beat = WorkerHeartbeat(clock=clock)
    beat.mark_blocked_strategies({7})
    clock.advance(600.0)

    beat.mark_blocked_strategies(set())

    assert beat.snapshot().strategy_blocked_sec == {}


def test_a_strategy_nobody_asked_this_round_is_not_reported_as_broken():
    """關市的時候 on_tick 策略不會被呼叫，那不是故障。

    這一條就是半夜那封信。把「沒有被問」記成「壞掉」的話，台股使用者從 13:30 到
    隔天 09:00 都會被警報器盯著，而它好得很——然後真的停擺的那一次，信長得一模
    一樣（test_the_watchdog_does_not_cry_wolf.py）。
    """
    clock = _FakeClock()
    beat = WorkerHeartbeat(clock=clock)
    beat.mark_blocked_strategies({7})
    clock.advance(600.0)

    # 這一輪只問了 8，7 根本沒輪到。
    beat.mark_blocked_strategies({8})

    assert set(beat.snapshot().strategy_blocked_sec) == {8}


def test_a_healthy_strategy_is_not_dragged_down_by_a_blind_one():
    """一格上的子行程死掉，另外兩格照跑——那兩支不可以跟著被算成停擺。"""
    clock = _FakeClock()
    beat = WorkerHeartbeat(clock=clock)

    beat.mark_blocked_strategies({7})
    clock.advance(600.0)

    assert set(beat.snapshot().strategy_blocked_sec) == {7}


# --- 門檻本身：半夜不要亂叫 --------------------------------------------------


def test_the_threshold_clears_the_closed_market_interval():
    """跟 HEALTH_MAX_AGE_SEC 同一條不變式。

    關市的時候輪詢間隔是 CLOSED_POLL_INTERVAL_SEC，所以一支真的持續壞掉的策略也
    要等到**第二次**被問到才會累積超過一個週期。門檻小於一個週期加一輪 tick 的
    話，一次失敗的重生就足以讓探測紅一次。
    """
    floor = market_loop.CLOSED_POLL_INTERVAL_SEC + settings.HEALTH_TICK_BUDGET_SEC

    assert settings.HEALTH_MAX_STRATEGY_BLOCKED_SEC > floor


# --- 盯盤迴圈有沒有記下來 ----------------------------------------------------


class _WorkerThatWillNotStart:
    """三個子行程一個都起不來的時候，池子對外就是這個樣子。"""

    def get_or_load(self, strategy_id, source_code, params=None):
        raise WorkerUnavailable("策略子行程沒有起來：{}")

    def invalidate(self, strategy_id):
        pass


class _HealthyHandle:
    entry_point = "on_tick"
    last_bar_ts = None

    def on_tick(self, price):
        return "HOLD"


class _WorkerThatWorks:
    def get_or_load(self, strategy_id, source_code, params=None):
        return _HealthyHandle()

    def invalidate(self, strategy_id):
        pass


TICK_SOURCE = """
class Strategy:
    def __init__(self):
        self.name = "quiet"
        self.symbol = "AAPL"

    def on_tick(self, current_price: float) -> str:
        return "HOLD"
"""


def _mock_service():
    return MarketDataService(
        providers={DataSource.YFINANCE: MockProvider(base_prices={"AAPL": 100.0})}
    )


def _a_strategy(db_session, *, email="blindspot@example.com", name="會壞掉的那支"):
    user = User(email=email, hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    strategy = Strategy(
        user_id=user.id,
        name=name,
        symbol="AAPL",
        source_code=TICK_SOURCE,
        code_hash=f"irrelevant-{email}",
        default_quantity=Decimal(1),
        is_active=True,
    )
    db_session.add(strategy)
    db_session.commit()
    db_session.refresh(strategy)
    return user, strategy


@pytest.fixture
def fake_heartbeat(monkeypatch):
    clock = _FakeClock()
    beat = WorkerHeartbeat(clock=clock)
    monkeypatch.setattr(worker_health, "heartbeat", beat)
    return beat, clock


def test_the_loop_records_a_strategy_it_could_not_run(db_session, monkeypatch, fake_heartbeat):
    beat, _ = fake_heartbeat
    _, strategy = _a_strategy(db_session)
    monkeypatch.setattr(market_loop, "_registry", _WorkerThatWillNotStart())

    market_loop.tick_once(db=db_session, market_data_service=_mock_service())

    assert set(beat.snapshot().strategy_blocked_sec) == {strategy.id}


def test_and_the_old_rule_still_holds(db_session, monkeypatch, fake_heartbeat):
    """看得見**不等於**開始怪罪使用者。

    子行程壞掉還是不累積、不停用（CLAUDE.md #18 第 3 條）。這一條放在這裡，是因
    為「讓它看得見」最容易的錯誤修法就是把它改回 _record_strategy_error。
    """
    _, strategy = _a_strategy(db_session)
    monkeypatch.setattr(market_loop, "_registry", _WorkerThatWillNotStart())

    for _ in range(6):
        market_loop.tick_once(db=db_session, market_data_service=_mock_service())

    db_session.refresh(strategy)
    assert strategy.is_active, "子行程起不來把使用者的策略停用了"
    assert strategy.consecutive_errors == 0


def test_and_stops_recording_it_once_the_worker_comes_back(db_session, monkeypatch, fake_heartbeat):
    beat, _ = fake_heartbeat
    _a_strategy(db_session)
    monkeypatch.setattr(market_loop, "_registry", _WorkerThatWillNotStart())
    market_loop.tick_once(db=db_session, market_data_service=_mock_service())

    monkeypatch.setattr(market_loop, "_registry", _WorkerThatWorks())
    market_loop.tick_once(db=db_session, market_data_service=_mock_service())

    assert beat.snapshot().strategy_blocked_sec == {}


# --- 公開探測（看門狗唯一讀得到的東西）---------------------------------------


def _healthz(client, monkeypatch, *, blocked_for: float, blocked=frozenset({1})):
    """一個「迴圈在轉、行情抓得到、但策略跑不起來」的部署。

    心跳在推進時間之後重新蓋章，是刻意的：這一組要問的是「其他每一項都正常的時
    候，策略停擺看不看得見」。不重新蓋章的話回應會因為 worker 那一格而紅，狀態碼
    就不再是這一組測到的東西。
    """
    clock = _FakeClock()
    beat = WorkerHeartbeat(clock=clock)
    beat.mark_blocked_strategies(set(blocked))
    clock.advance(blocked_for)
    beat.mark_loop()
    beat.mark_poll_success()

    monkeypatch.setattr(settings, "WORKER_ENABLED", True)
    # conftest 把這兩個關掉，好讓整套測試不會真的起迴圈或送東西出去。留著關的話
    # 每一個回應都是 503，而理由跟策略一點關係都沒有。
    monkeypatch.setattr(settings, "NOTIFICATIONS_ENABLED", True)
    monkeypatch.setattr(worker_health, "heartbeat", beat)
    # **看門狗看的是深的那一條。** `/healthz` 沒帶參數的時候只回答「重開這台機器有沒有
    # 機會修好」——Render 的健康檢查看的是它，而它失敗 60 秒就會把行程重開（見
    # test_the_probe_render_watches_cannot_restart_him_forever）。這裡問的是「有沒有人
    # 會被通知」，那是 ?deep=1。
    return client.get("/healthz", params={"deep": "1"})


def test_a_brief_outage_does_not_page_anyone(client, monkeypatch):
    """一次 spawn 失敗，下一輪就重生了。那不是停擺。"""
    assert _healthz(client, monkeypatch, blocked_for=30.0).status_code == 200


def test_strategies_that_have_been_blind_for_a_long_time_turn_the_probe_red(client, monkeypatch):
    resp = _healthz(client, monkeypatch, blocked_for=settings.HEALTH_MAX_STRATEGY_BLOCKED_SEC + 1)

    assert resp.status_code == 503
    assert resp.json()["checks"]["strategies"]["status"] == "fail"


def test_the_probe_counts_them_but_does_not_name_them(client, monkeypatch):
    """/healthz 沒有憑證也打得到（render.yaml 的健康檢查和看門狗都靠它），所以
    它只說「幾支」。是哪一支要登入才看得到——跟代號那一格同一條規則。"""
    resp = _healthz(
        client,
        monkeypatch,
        blocked_for=settings.HEALTH_MAX_STRATEGY_BLOCKED_SEC + 1,
        blocked=frozenset({987654, 987655}),
    )

    assert resp.json()["checks"]["strategies"]["blocked_count"] == 2
    assert "987654" not in resp.text


def test_the_check_is_disabled_along_with_the_worker(client, monkeypatch):
    """WORKER_ENABLED 關著的時候什麼都不會跑，worker 那一格已經說了。"""
    monkeypatch.setattr(settings, "WORKER_ENABLED", False)

    assert client.get("/healthz").json()["checks"]["strategies"]["status"] == "disabled"


# --- 狀態頁：是哪一支 --------------------------------------------------------


def _blind_deployment(monkeypatch, blocked: set[int]):
    clock = _FakeClock()
    beat = WorkerHeartbeat(clock=clock)
    beat.mark_blocked_strategies(blocked)
    clock.advance(settings.HEALTH_MAX_STRATEGY_BLOCKED_SEC + 1)
    beat.mark_loop()
    beat.mark_poll_success()
    monkeypatch.setattr(settings, "WORKER_ENABLED", True)
    monkeypatch.setattr(settings, "NOTIFICATIONS_ENABLED", True)
    monkeypatch.setattr(worker_health, "heartbeat", beat)


def test_the_owner_can_see_which_strategy_is_blocked(auth_client, db_session, monkeypatch):
    """「有東西壞了」不是一個人可以拿去做事的句子。"""
    owner = db_session.query(User).filter(User.email == "fixture-user@example.com").one()
    strategy = Strategy(
        user_id=owner.id,
        name="我的均線策略",
        symbol="AAPL",
        source_code=TICK_SOURCE,
        code_hash="hash-status-page",
        default_quantity=Decimal(1),
    )
    db_session.add(strategy)
    db_session.commit()
    db_session.refresh(strategy)
    _blind_deployment(monkeypatch, {strategy.id})

    body = auth_client.get("/api/system/status").json()

    blocked = body["strategies"]["blocked"]
    assert [row["strategy_id"] for row in blocked] == [strategy.id]
    assert blocked[0]["name"] == "我的均線策略"
    assert body["overall"] == "fail"


def test_somebody_elses_blocked_strategy_is_not_your_business(auth_client, db_session, monkeypatch):
    """heartbeat 是行程層級的單例，它的表是跨全部帳號的聯集。

    跟 stale_symbols 一樣要按帳號濾（tests/test_the_status_page_shows_only_your_symbols.py）：
    別人寫了什麼策略不是你的事，而且那是一個你按不動的紅燈。
    """
    _, other = _a_strategy(db_session, email="someone-else@example.com", name="別人的策略")
    _blind_deployment(monkeypatch, {other.id})

    response = auth_client.get("/api/system/status")
    body = response.json()

    assert body["strategies"]["blocked"] == []
    assert "別人的策略" not in response.text
    assert body["overall"] != "fail"


# --- 那封信 ------------------------------------------------------------------


def test_the_watchdog_says_what_a_dead_strategy_worker_means():
    """信裡不可以只寫「strategies 檢查失敗」。收信的人不是工程師。"""
    from scripts.watchdog import read_verdict

    problems = read_verdict(
        503,
        '{"status": "fail", "checks": {"strategies": {"status": "fail", "blocked_count": 3}}}',
    )

    assert len(problems) == 1
    assert "提醒" in problems[0], problems[0]
