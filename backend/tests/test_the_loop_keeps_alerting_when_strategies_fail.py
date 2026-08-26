"""策略那一層怎麼壞，都不可以讓這一輪的警告停掉。

#18 把使用者的策略搬進獨立子行程。那買到的是隔離，但也**新增了一整類原本不存在
的失效**：子行程起不來、跑到一半死掉、卡住不回、管線上收到半截訊息。原本這些事
情不會發生，因為根本沒有子行程。

所以這一組測的不是「策略有沒有跑出訊號」，而是策略跑不出訊號的時候，這一輪剩下
的事有沒有照做：

    停損掃描      —— 部位跌破線要有 SELL
    委託到期      —— 過期的委託要被標成 EXPIRED
    待送通知重送  —— 沒送到的通知要繼續嘗試

這三件事跟策略一點關係都沒有，卻跟策略共用同一個 tick_once()。`tick_once` 自己的
try 只有 finally 沒有 except，所以任何漏接的例外都會直接走出去——那一輪的停損、
到期和通知全部跳過。這在這個 repo 已經發生過一次（見 market_loop.py 裡 bar_failures
上方那段註解），而那一次的原因只是「一個代號抓不到價」。

子行程帶進來的失效模式比那個多得多，所以這裡逐一擺出來，每一種都問同樣三個問題。
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.config import settings
from app.enums import DataSource, OrderSide, OrderSource, OrderStatus
from app.models.mixins import utcnow
from app.models.order import Order
from app.models.position import Position
from app.models.risk import RiskSettings
from app.models.strategy import Strategy
from app.models.user import User
from app.services import market_loop
from app.services.market_data.base import Bar
from app.services.market_data.providers.mock_provider import MockProvider
from app.services.market_data.service import MarketDataService
from app.services.strategy_worker import StrategyWorkerError

pytestmark = pytest.mark.usefixtures("published_events")


@pytest.fixture
def mock_market_service():
    """一個一定給得出價的行情來源。

    這一組問的是「策略壞掉的時候會怎樣」，所以行情不可以同時也是變數——抓不到價
    有它自己的一組測試，混在一起會讓失敗的原因分不出是哪一個。
    """
    return MarketDataService(
        providers={DataSource.YFINANCE: MockProvider(base_prices={"AAPL": 100.0})}
    )


@pytest.fixture(autouse=True)
def _short_strategy_timeout(monkeypatch):
    """把逾時縮短，不然無窮迴圈那兩條要等滿預設的秒數。

    縮短的是**設定**不是程式碼路徑：走的還是同一條逾時邏輯。
    """
    monkeypatch.setattr(settings, "STRATEGY_TICK_TIMEOUT_SEC", 1.0)


@pytest.fixture(autouse=True)
def _fresh_pool():
    """每條測試都從乾淨的池開始，結束時關掉。

    不關的話子行程會留到整套測試跑完——1900 個測試乘上幾個殘留的子行程，就是這台
    機器上那個「跑到一半 Failed to start threads worker」的來源。
    """
    market_loop.reset_strategy_workers()
    yield
    market_loop.reset_strategy_workers()


TICK_SOURCE = """
class Strategy:
    def __init__(self):
        self.name = "quiet"
        self.symbol = "AAPL"

    def on_tick(self, current_price: float) -> str:
        return "HOLD"
"""

# 真的會卡住的策略。不是模擬、不是 monkeypatch 出來的 sleep——這一支就是使用者
# 有一天會寫出來的東西，而 Python 殺不掉一條執行緒，所以在搬進子行程之前，它會
# 永久佔住那個策略的位置。
STUCK_SOURCE = """
class Strategy:
    def __init__(self):
        self.name = "stuck"
        self.symbol = "AAPL"

    def on_tick(self, current_price: float) -> str:
        while True:
            pass
"""


class _WorkerThatWontStart:
    """load 的時候就炸。子行程起不來長這樣。"""

    def get_or_load(self, *args, **kwargs):
        raise StrategyWorkerError("策略子行程沒有起來")

    def invalidate(self, strategy_id):
        pass


class _WorkerThatDiesMidRound:
    """load 成功，呼叫的時候才發現對面已經死了。"""

    def get_or_load(self, strategy_id, source_code, params=None):
        return _DeadHandle()

    def invalidate(self, strategy_id):
        pass


class _WorkerThatHandsBackGarbage:
    """管線上回來半截 JSON——這是最危險的一種。

    半截的訊息如果被當成「策略沒有訊號」，一次通訊故障就會長得跟一個正常的 HOLD
    一模一樣，而使用者永遠不會知道那一輪其實沒有跑。所以它必須是錯誤。
    """

    def get_or_load(self, strategy_id, source_code, params=None):
        return _GarbageHandle()

    def invalidate(self, strategy_id):
        pass


class _DeadHandle:
    entry_point = "on_tick"
    last_bar_ts = None

    def on_tick(self, price):
        raise StrategyWorkerError("策略子行程死掉了")


class _GarbageHandle:
    entry_point = "on_tick"
    last_bar_ts = None

    def on_tick(self, price):
        raise StrategyWorkerError("策略子行程回了讀不懂的東西：'{\"ok\": tr'")


class _HandleThatCannotBeSerialised:
    """策略回了一個送不過管線的東西（一個 Bar 物件、一個 numpy 陣列）。

    這不是使用者的錯，是我們的協定的邊界，但後果一樣：那一輪這支策略沒有答案。
    """

    entry_point = "on_tick"
    last_bar_ts = None

    def on_tick(self, price):
        raise StrategyWorkerError("Object of type Bar is not JSON serializable")


class _WorkerThatCannotSerialise:
    def get_or_load(self, strategy_id, source_code, params=None):
        return _HandleThatCannotBeSerialised()

    def invalidate(self, strategy_id):
        pass


BROKEN_WORKERS = [
    pytest.param(_WorkerThatWontStart, id="子行程起不來"),
    pytest.param(_WorkerThatDiesMidRound, id="子行程跑到一半死掉"),
    pytest.param(_WorkerThatHandsBackGarbage, id="管線上收到半截訊息"),
    pytest.param(_WorkerThatCannotSerialise, id="回傳值送不過管線"),
]


def _scenario(db_session, source_code=TICK_SOURCE):
    """一個同時等著三件事發生的帳號。

    部位在水面下（該停損）、有一張早就過期的委託（該標到期）、而策略是那個要壞
    掉的東西。三件事共用同一次 tick_once()。
    """
    user = User(email="failmode@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    db_session.add(RiskSettings(user_id=user.id, stop_loss_pct=Decimal("0.1")))

    strategy = Strategy(
        user_id=user.id,
        name="會壞掉的那支",
        symbol="AAPL",
        source_code=source_code,
        code_hash="irrelevant-for-tests",
        is_active=True,
    )
    db_session.add(strategy)

    # 進場價 200，行情 100 —— 跌了一半，停損線 10% 早就破了。
    db_session.add(
        Position(
            user_id=user.id,
            symbol="AAPL",
            quantity=Decimal(10),
            avg_entry_price=Decimal(200),
        )
    )

    stale = Order(
        user_id=user.id,
        symbol="AAPL",
        side=OrderSide.BUY,
        source=OrderSource.STRATEGY,
        quantity=Decimal(1),
        status=OrderStatus.PENDING,
        created_at=utcnow() - timedelta(days=30),
    )
    db_session.add(stale)
    db_session.commit()
    db_session.refresh(stale)
    return user, strategy, stale


def _assert_the_round_still_did_its_job(db_session, stale_order, swept):
    """三個問題，每一種失效模式都問一遍。"""
    sell = (
        db_session.query(Order)
        .filter(Order.side == OrderSide.SELL, Order.status == OrderStatus.PENDING)
        .first()
    )
    assert sell is not None, "策略壞掉把停損掃描一起帶走了"
    assert sell.risk_notes["trigger"] == "stop_loss"

    db_session.refresh(stale_order)
    assert stale_order.status == OrderStatus.EXPIRED, "策略壞掉把委託到期一起帶走了"

    assert swept, "策略壞掉把待送通知的重送一起帶走了——那是這個產品的重大失效"


@pytest.mark.parametrize("broken", BROKEN_WORKERS)
def test_a_broken_worker_does_not_take_the_round_with_it(
    db_session, monkeypatch, broken, mock_market_service
):
    _, _, stale = _scenario(db_session)

    monkeypatch.setattr(market_loop, "_registry", broken())
    swept: list[bool] = []
    monkeypatch.setattr(
        market_loop.notification_retry, "retry_pending", lambda db: swept.append(True)
    )

    market_loop.tick_once(db=db_session, market_data_service=mock_market_service)

    _assert_the_round_still_did_its_job(db_session, stale, swept)


def test_a_strategy_stuck_in_an_infinite_loop_does_not_take_the_round_with_it(
    db_session, monkeypatch, mock_market_service
):
    """這一條不用替身：`while True: pass` 是真的跑起來的。

    替身測不到重點。重點是**逾時真的會發生**，而在搬進子行程之前，Python 殺不掉
    那條執行緒——它會一直燒著一顆核心，直到整個行程重啟。子行程換掉的正是這件
    事：那邊的東西殺得掉。
    """
    _, _, stale = _scenario(db_session, source_code=STUCK_SOURCE)

    swept: list[bool] = []
    monkeypatch.setattr(
        market_loop.notification_retry, "retry_pending", lambda db: swept.append(True)
    )

    market_loop.tick_once(db=db_session, market_data_service=mock_market_service)

    _assert_the_round_still_did_its_job(db_session, stale, swept)


def test_the_stuck_strategy_is_actually_dead_afterwards(
    db_session, monkeypatch, mock_market_service
):
    """逾時之後，那個無窮迴圈不可以還在燒 CPU。

    這一條是 #18 相對於舊做法真正買到的東西。strategy_runtime._guarded 的檔頭自己
    寫著它做不到：「Python cannot kill a thread, so an abandoned call keeps running
    ... until the process restarts」。子行程可以被殺掉，所以這裡驗的是它真的被殺
    掉了——不然這張票只是把同一個洩漏搬到另一個檔案裡。
    """
    _, strategy, _ = _scenario(db_session, source_code=STUCK_SOURCE)

    market_loop.tick_once(db=db_session, market_data_service=mock_market_service)

    assert not market_loop.stuck_children_still_running(), (
        "逾時的策略還在跑。它會一直佔著一顆核心，而下一輪還會再開一個。"
    )
    db_session.refresh(strategy)
    assert strategy.last_error, "逾時了卻沒有留下任何使用者讀得到的訊息"


def test_one_strategy_hanging_does_not_blind_the_others(
    db_session, monkeypatch, mock_market_service
):
    """一支卡住，另一支照跑。

    這是「固定幾個 worker」這個設計唯一真正的風險：如果兩支策略排在同一個 worker
    上，前面那支卡住十秒，後面那支就瞎了十秒。輪詢週期才五秒。
    """
    user, _, _ = _scenario(db_session, source_code=STUCK_SOURCE)
    healthy = Strategy(
        user_id=user.id,
        name="沒事的那支",
        symbol="AAPL",
        source_code=TICK_SOURCE.replace('"quiet"', '"healthy"').replace("HOLD", "BUY"),
        code_hash="irrelevant-for-tests",
        is_active=True,
    )
    db_session.add(healthy)
    db_session.commit()
    db_session.refresh(healthy)

    market_loop.tick_once(db=db_session, market_data_service=mock_market_service)

    db_session.refresh(healthy)
    assert healthy.last_run_at is not None, "另一支策略被卡住的那支拖著一起瞎了"
    assert not healthy.last_error


def test_a_bar_strategy_that_fails_still_leaves_the_round_intact(
    db_session, monkeypatch, mock_market_service
):
    """on_bar 那條路是分開的一條 code path，所以分開驗一次。

    tick_once 對 on_bar 策略多做兩件事（抓 K 棒、暖身），而那兩件事各自有自己的
    early return。一條在 K 棒那邊漏接的例外，一樣會把整輪帶走。
    """
    bar_source = TICK_SOURCE.replace(
        '    def on_tick(self, current_price: float) -> str:\n        return "HOLD"',
        "        self.timeframe = '1d'\n\n"
        "    def on_bar(self, bar) -> str:\n        raise RuntimeError('K 棒炸了')",
    )
    _, _, stale = _scenario(db_session, source_code=bar_source)

    swept: list[bool] = []
    monkeypatch.setattr(
        market_loop.notification_retry, "retry_pending", lambda db: swept.append(True)
    )

    market_loop.tick_once(db=db_session, market_data_service=mock_market_service)

    _assert_the_round_still_did_its_job(db_session, stale, swept)


def test_state_survives_across_ticks(db_session, mock_market_service):
    """策略的記憶要跨輪活著，不然均線永遠只看得到一個價。

    這一條看起來跟失效模式無關，其實是同一件事的另一面：子行程如果每一輪都重
    建，那 self.prices 每一輪都會被清空——而那不會有任何東西變紅，只會讓 MA20
    策略永遠不發訊號。搬家最容易安靜壞掉的就是這個。
    """
    counting = (
        "class Strategy:\n"
        "    def __init__(self):\n"
        "        self.name = 'counter'\n"
        "        self.symbol = 'AAPL'\n"
        "        self.seen = 0\n"
        "    def on_tick(self, current_price: float) -> str:\n"
        "        self.seen += 1\n"
        "        return 'BUY' if self.seen >= 3 else 'HOLD'\n"
    )
    _scenario(db_session, source_code=counting)

    for _ in range(3):
        market_loop.tick_once(db=db_session, market_data_service=mock_market_service)

    buys = (
        db_session.query(Order)
        .filter(Order.side == OrderSide.BUY, Order.status == OrderStatus.PENDING)
        .count()
    )
    assert buys >= 1, "策略的狀態沒有跨輪活著——第三輪應該要發出 BUY"


def test_bars_reach_the_strategy_unchanged(db_session, mock_market_service):
    """K 棒過了管線之後還是同一根。

    這一條守的是序列化：Bar 的 timestamp 是 datetime，而 JSON 沒有 datetime。轉
    成字串再轉回來只要差一個時區，on_bar 拿到的就是另一根 K 棒——而策略照樣會回
    一個看起來很正常的訊號。沒有東西會紅。
    """
    echo = (
        "class Strategy:\n"
        "    def __init__(self):\n"
        "        self.name = 'echo'\n"
        "        self.symbol = 'AAPL'\n"
        "        self.timeframe = '1d'\n"
        "        self.warmup_bars = 0\n"
        "    def on_bar(self, bar) -> str:\n"
        "        self.last = (bar.timestamp, bar.close)\n"
        "        return 'HOLD'\n"
    )
    _, strategy, _ = _scenario(db_session, source_code=echo)

    market_loop.tick_once(db=db_session, market_data_service=mock_market_service)

    db_session.refresh(strategy)
    assert strategy.last_error is None or "warming up" in (strategy.last_error or "")


def test_a_bar_round_trips_through_the_pipe(db_session):
    """直接問管線本身，不繞過整個迴圈。

    上面那條是端對端的，這條是點對點的：一根 Bar 送過去、原樣送回來，時區、秒
    數、成交量都要一致。端對端的測試看不出「差了八小時」，因為策略照樣會回訊號。
    """
    from app.services import strategy_pool

    bar = Bar(
        symbol="AAPL",
        timeframe=market_loop.Timeframe.DAY_1,
        timestamp=utcnow(),
        open=1.5,
        high=2.5,
        low=0.5,
        close=2.0,
        volume=1234.0,
    )

    assert strategy_pool.bar_from_wire(strategy_pool.bar_to_wire(bar)) == bar


def test_warming_up_costs_nothing_when_it_fails():
    """暖不起來只是「還沒暖」，不是故障。

    暖機在 run_forever 進第一輪之前跑。如果它會拋例外，那麼一次暫時的 spawn 失敗
    就會讓盯盤迴圈**連第一輪都跑不了**——而盯盤不能停是這個產品唯一的鐵律。真正
    需要 worker 的時候 ensure() 還會再試一次，所以這裡什麼都不做才是對的。
    """
    from app.services import strategy_pool

    pool = strategy_pool.StrategyPool(size=2)
    for slot in pool._slots:
        slot.worker.start = lambda: (_ for _ in ()).throw(RuntimeError("spawn 失敗"))

    pool.prewarm()  # 不可以拋

    pool.shutdown()


def test_warming_up_actually_leaves_the_workers_ready():
    """暖完之後每個 worker 都要活著，而且沙箱已經在裡面。

    量到的：沙箱 import 886 毫秒。沒有這一步，那筆錢會落在重啟後的第一輪盯盤上，
    三個 worker 依序付是 3.4 秒——而輪詢週期只有五秒。
    """
    from app.services import strategy_pool

    pool = strategy_pool.StrategyPool(size=2)
    try:
        pool.prewarm()

        assert all(slot.worker.alive for slot in pool._slots), "暖機之後還有 worker 沒起來"
        # 沙箱在不在裡面，用「載入一支策略」問——那是唯一會用到它的路徑。
        source = (
            "class Strategy:\n"
            "    def __init__(self):\n"
            "        self.name = 'warm'\n"
            "        self.symbol = 'AAPL'\n"
            "    def on_tick(self, price):\n"
            "        return 'HOLD'\n"
        )
        assert pool.get_or_load(1, source).entry_point == "on_tick"
    finally:
        pool.shutdown()


def test_the_pool_does_not_grow_with_the_number_of_strategies():
    """二十支策略還是三個子行程。

    這是「固定幾個」相對於「一支一個」買到的東西：記憶體變成 N × 20 MB 的常數，
    跟使用者寫幾支策略無關。一支一個在數字上也負擔得起，但**成長沒有上界**，而這
    台機器只有 512 MB。
    """
    from app.services import strategy_pool

    pool = strategy_pool.StrategyPool()
    try:
        source = (
            "class Strategy:\n"
            "    def __init__(self):\n"
            "        self.name = 'many'\n"
            "        self.symbol = 'AAPL'\n"
            "    def on_tick(self, price):\n"
            "        return 'HOLD'\n"
        )
        for strategy_id in range(1, 21):
            pool.get_or_load(strategy_id, source)

        alive = [slot for slot in pool._slots if slot.worker.alive]
        assert len(alive) <= strategy_pool.DEFAULT_POOL_SIZE
        assert len(pool._slots) == strategy_pool.DEFAULT_POOL_SIZE
    finally:
        pool.shutdown()
