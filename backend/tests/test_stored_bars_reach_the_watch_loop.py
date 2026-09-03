"""存下來的 K 棒要走得到盯盤那條路，不是只有圖表用得到。

#38 把 K 棒存了下來，但只有 `/bars` 那個路由帶 `db` 進 `MarketDataService.get_bars`。
盯盤迴圈（`market_loop.tick_once`）沒有帶，於是 `_prime_from_storage` 第一行就
return——四件事一起發生，而且沒有任何一件會讓東西變紅：

一、**存量到不了策略。** Render 免費方案閒置就休眠，醒來就是一個全新的行程、一份
    空的記憶體快取。上游這時候若不通，盯盤那條路拿到的是空清單。

二、**空清單被讀成「還在暖身」。** `_run_bar_strategy` 只看 `len(bars) < warmup`，
    所以那一列寫的是「warming up: 0/3」——一句會讓人以為再等一下就好的話，而實際
    上是行情斷了、這支策略一則提醒都不會發。**警告不能停擺是最高優先**，而這是停
    擺得最安靜的那一種：畫面看起來正常，它正在說謊。

三、**warmup 為 0 的策略連那句謊話都到不了，它直接被停用。** `len([]) < 0` 是
    False，所以空清單掉進 `bars[-1]` → IndexError → `_record_strategy_error` →
    連續五次 → `is_active = False`，輪詢五秒一次，**二十五秒後永久停用，沒有東西會
    把它打開**。而「不用寫 Python 就能設定的簡單價格提醒」正是這個產品的核心功能，
    那種策略的 warmup 就是 0。

四、**迴圈自己抓到的 K 棒沒有存回去。** 圖表只會存有人打開過的那幾個週期；策略用
    的週期（1wk、1h…）可能從來沒有人看過圖，那個週期就永遠沒有底可以退。
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.enums import DataSource
from app.models.strategy import Strategy
from app.models.user import User
from app.services import bar_store, market_loop
from app.services.market_data.base import Bar, BarFetchError, Quote, Timeframe
from app.services.market_data.service import MarketDataService
from app.services.strategy_runtime import StrategyRegistry

_START = datetime(2026, 1, 5, tzinfo=UTC)

WEEKLY_WATCHER = """
class Strategy:
    def __init__(self):
        self.name = "weekly_watcher"
        self.symbol = "2330.TW"
        self.timeframe = "1wk"
        self.warmup_bars = 1
        self.seen = []

    def on_bar(self, bar) -> str:
        self.seen.append(bar.close)
        return "HOLD"
"""

# 同一支，只差 warmup 宣告成 0——那是「跌到 900 叫我」這種提醒的形狀，也是這個 app
# 的核心功能。
NO_WARMUP_WATCHER = WEEKLY_WATCHER.replace("self.warmup_bars = 1", "self.warmup_bars = 0")


@pytest.fixture(autouse=True)
def _fresh_registry(monkeypatch):
    """策略實例是模組層級的，跨 tick 活著——跨「測試」活著就會變成上一條的暖身狀態
    回答下一條的第一次輪詢。"""
    monkeypatch.setattr(market_loop, "_registry", StrategyRegistry())


class _Provider:
    """一個可以切成「抓得到」或「抓不到」的假上游。

    抓不到丟的是 `BarFetchError`：那是**服務層自己會吞掉**的那一種（換成 stale
    cache），所以它不會走到 tick_once 的 `bar_failures`，而是安靜地變成一份空清
    單。這正是這個缺口能躲這麼久的原因。
    """

    data_source = DataSource.YFINANCE

    def __init__(self, closes: list[float], *, fails: bool = False) -> None:
        self.closes = list(closes)
        self.fails = fails

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        return {
            s: Quote(symbol=s, data_source=self.data_source, price=Decimal(500)) for s in symbols
        }

    def get_bars(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Bar]:
        if self.fails:
            raise BarFetchError("上游擋住了")
        return _bars(symbol, timeframe, self.closes)


def _bars(symbol: str, timeframe: Timeframe, closes: list[float]) -> list[Bar]:
    return [
        Bar(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=_START + timedelta(weeks=i),
            open=close,
            high=close + 1,
            low=close - 1,
            close=close,
            volume=1000.0,
        )
        for i, close in enumerate(closes)
    ]


def _service(provider: _Provider) -> MarketDataService:
    return MarketDataService(providers={DataSource.YFINANCE: provider})


def _make_strategy(db_session, source: str = WEEKLY_WATCHER) -> Strategy:
    user = User(email="stored-bars@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    strategy = Strategy(
        user_id=user.id,
        name="weekly-watcher",
        symbol="2330.TW",
        source_code=source,
        code_hash="irrelevant-for-tests",
        is_active=True,
    )
    db_session.add(strategy)
    db_session.commit()
    db_session.refresh(strategy)
    return strategy


def _seen_by(strategy: Strategy) -> list[float]:
    return market_loop._registry.get_or_load(strategy.id, strategy.source_code).instance.seen


def test_a_warmup_zero_strategy_is_not_disabled_when_the_feed_dies(db_session):
    """行情斷了，不可以在二十五秒後把他的策略永久停用。

    這一條守的是最嚴重的那一種，而且它在**這份修法之前就已經成立**：warmup 宣告成 0
    的時候 `len([]) < 0` 是 False，所以空清單連「warming up」那個分支都進不去，直接
    掉到 `bars[-1]` → IndexError → `_record_strategy_error` → 連續五次 →
    `is_active = False`。輪詢五秒一次，所以是二十五秒；而沒有任何東西會把它打開，畫
    面只寫「停用」。

    上游斷線是我們這一邊的事，不是他的程式碼有問題——CLAUDE.md 子行程那五條的第三
    條講的是同一件事：基礎設施壞掉不可以走停用那條路。

    跑六輪是刻意的：`_MAX_CONSECUTIVE_ERRORS` 是 5，所以第五輪才剛好踩到，第六輪確
    認它不是「還沒數到」。
    """
    strategy = _make_strategy(db_session, NO_WARMUP_WATCHER)
    service = _service(_Provider([], fails=True))

    for _ in range(6):
        market_loop.tick_once(db=db_session, market_data_service=service)

    db_session.refresh(strategy)
    assert strategy.is_active is True, (
        f"上游斷線把策略停用了：consecutive_errors={strategy.consecutive_errors}，"
        f"last_error={strategy.last_error!r}。沒有任何東西會把它打開。"
    )
    assert strategy.consecutive_errors == 0, "行情抓不到不是策略的錯，不可以累積"


def test_the_watch_loop_falls_back_to_stored_bars_when_the_feed_is_down(db_session):
    """休眠醒來、上游不通——存下來的就是為了這一刻。

    存量餵得進去，策略就是暖的：上游一恢復，下一根收好的 K 棒立刻能發訊號，而不是
    再從零暖身一次。
    """
    strategy = _make_strategy(db_session)
    bar_store.save(
        db_session,
        DataSource.YFINANCE,
        "2330.TW",
        Timeframe.WEEK_1,
        _bars("2330.TW", Timeframe.WEEK_1, [100.0, 101.0, 102.0]),
    )
    db_session.commit()

    market_loop.tick_once(db=db_session, market_data_service=_service(_Provider([], fails=True)))

    assert _seen_by(strategy) == [100.0, 101.0, 102.0], "存下來的 K 棒要真的餵進策略"
    db_session.refresh(strategy)
    assert strategy.last_error is None, "手上有三根，不是「還在暖身」"


def test_a_dead_feed_with_nothing_stored_does_not_read_as_warming_up(db_session):
    """沒有存量、上游也不通的時候，那一列不可以寫「還在暖身」。

    暖身是一句會自己過去的話，行情斷了不會。兩者混在一起，使用者會坐在那裡等一個
    永遠不會來的提醒。
    """
    strategy = _make_strategy(db_session)

    market_loop.tick_once(db=db_session, market_data_service=_service(_Provider([], fails=True)))

    db_session.refresh(strategy)
    assert strategy.last_error, "抓不到就要說出來，不可以什麼都不寫"
    assert "warming up" not in strategy.last_error, "行情斷了不是暖身"
    assert "抓不到" in strategy.last_error
    # 抓不到不是策略的錯：走 _record_strategy_error 的話，五輪（二十五秒）之後
    # 使用者每一支策略都會被永久停用，而且沒有東西會把它們打開。
    assert strategy.consecutive_errors == 0
    assert strategy.is_active is True


def test_the_watch_loop_writes_down_what_it_fetched(db_session):
    """圖表只存有人打開過的週期。策略用的那個週期要由迴圈自己存，不然永遠沒有底。"""
    _make_strategy(db_session)

    market_loop.tick_once(
        db=db_session, market_data_service=_service(_Provider([100.0, 101.0, 102.0]))
    )

    stored = bar_store.load(db_session, DataSource.YFINANCE, "2330.TW", Timeframe.WEEK_1, 99)
    assert [b.close for b in stored] == [100.0, 101.0, 102.0]


def test_what_the_loop_stored_survives_the_session_that_stored_it(db_session):
    """flush 不是存下來。

    `bar_store.save` 只 flush，而 `get_db` 在請求結束時 close——close 會把沒 commit
    的東西丟掉。少了那一次 commit，「重開機之後還畫得出來」是一個永遠不成立的承
    諾，而且不會有任何東西變紅：同一個 session 讀得到，所以測試也看不出來。
    """
    _make_strategy(db_session)
    engine = db_session.get_bind()

    market_loop.tick_once(
        db=db_session, market_data_service=_service(_Provider([100.0, 101.0, 102.0]))
    )

    with Session(bind=engine) as elsewhere:
        stored = bar_store.load(elsewhere, DataSource.YFINANCE, "2330.TW", Timeframe.WEEK_1, 99)
    assert len(stored) == 3, "另一條連線看得到，才叫存下來"


def test_a_chart_fetch_is_still_there_after_the_request_has_ended(db_session):
    """同一件事，走圖表那條路——`/bars` 的 session 是請求結束就 close 的那一種。"""
    engine = db_session.get_bind()
    service = _service(_Provider([100.0, 101.0, 102.0]))

    request_scoped = Session(bind=engine)
    service.get_bars("2330.TW", Timeframe.WEEK_1, DataSource.YFINANCE, limit=3, db=request_scoped)
    request_scoped.close()  # get_db 的 finally 就是這一行

    with Session(bind=engine) as elsewhere:
        stored = bar_store.load(elsewhere, DataSource.YFINANCE, "2330.TW", Timeframe.WEEK_1, 99)
    assert len(stored) == 3, "請求結束就被丟掉的話，資料庫裡永遠是空的"


def test_a_storage_failure_does_not_discard_the_callers_own_work(db_session, monkeypatch):
    """存不進 K 棒，不可以把呼叫端手上還沒送出去的東西一起丟掉。

    `_fetch_bars` 存檔失敗時會 `db.rollback()`，而那個 rollback 回滾的是**整個
    session**，不只是這一次存檔。在盯盤迴圈把自己的 session 借進來之前，那個 rollback
    只會丟掉一個唯讀請求的東西，所以沒有人看得出來；借進來之後，被丟掉的就有可能是那
    一輪已經算出來、還沒送出去的訊號、Order 和通知紀錄。

    **警告不能停擺，優先於一根 K 棒存不存得下來。** 所以借來的 session 上的東西要先
    送出去再存，回滾最多只丟得掉這一次存檔自己。

    這裡用一列不相干的資料當作「還沒送出去的東西」：測的是那個借用契約，不是某一輪
    剛好會產生什麼。
    """
    engine = db_session.get_bind()
    db_session.add(User(email="lender@example.com", hashed_password="x"))

    def disk_is_full(*args, **kwargs):
        raise RuntimeError("磁碟滿了")

    monkeypatch.setattr(bar_store, "save", disk_is_full)

    _service(_Provider([100.0, 101.0])).get_bars(
        "2330.TW", Timeframe.WEEK_1, DataSource.YFINANCE, limit=2, db=db_session
    )

    with Session(bind=engine) as elsewhere:
        survived = elsewhere.query(User).filter(User.email == "lender@example.com").count()
    assert survived == 1, (
        "存不進 K 棒，連呼叫端還沒送出去的東西也一起被回滾掉了。盯盤迴圈把 session 借進來"
        "之後，這裡丟掉的會是那一輪的提醒。"
    )


THREE_BAR_WARMUP = WEEKLY_WATCHER.replace("self.warmup_bars = 1", "self.warmup_bars = 3")


def test_a_half_warm_strategy_says_the_feed_is_down_too(db_session):
    """手上有幾根、但不夠暖身，而且上游同時斷線——不可以只寫「warming up」。

    「warming up: 2/3」是一句會自己過去的話：再收一根就好了。可是上游斷著的時候那一
    根不會來，所以同一句話變成了謊——他會坐在那裡等一個永遠不會到的提醒，而畫面上一
    切正常。

    空清單那一種已經擋在抓取端了；這是剩下的那一半：**手上有東西，但那些東西不會再
    增加。** 兩種要說的是同一件事。
    """
    strategy = _make_strategy(db_session, THREE_BAR_WARMUP)
    bar_store.save(
        db_session,
        DataSource.YFINANCE,
        "2330.TW",
        Timeframe.WEEK_1,
        _bars("2330.TW", Timeframe.WEEK_1, [100.0, 101.0]),
    )
    db_session.commit()

    market_loop.tick_once(db=db_session, market_data_service=_service(_Provider([], fails=True)))

    db_session.refresh(strategy)
    assert strategy.last_error
    assert "抓不到" in strategy.last_error, (
        f"上游斷著，那一列卻只寫了 {strategy.last_error!r}——那句話說的是「再等一下就好」。"
    )
    # 手上有幾根還是要講，不然使用者不知道恢復之後還要等多久。
    assert "2/3" in strategy.last_error
    assert strategy.consecutive_errors == 0
    assert strategy.is_active is True
