"""#60 封住了每一段序列的深度，沒有封住**序列的數量**。

那一份的檔頭自己寫著這句話，而且指向這裡：「看過一次就不再更新的那些段依然永遠躺
在那裡，見 #61。不要把這一組測試讀成『這張表現在有上限了』。」

＊ 剩下的成長項。

每一個（來源、代號、週期）組合，只要他曾經看過一次那張圖，就永久留下一組列。他半
年前查過一次的代號，那一段停在當時的大小——不會再長，也不會被刪，而整個 repo 沒有
任何端點、腳本或排程刪得掉它。

    60 個代號 × 10 個週期 = 600 段 × 1000 列 = 60 萬列，永遠躺著。

空間用完的後果不是「圖變醜」：免費方案整個資料庫 0.5 GB，塞滿之後失敗的是**寫
入**——通知紀錄、重送佇列、部位，全部一起。而他手上沒有 psql，app 裡也沒有任何一顆
按鈕能把空間拿回來。

＊ 判準是「多久沒有被抓過」，不是「多舊」。

`MarketBar.fetched_at` 是那一列**被寫進來**的時間，跟 `ts`（那根 K 棒是什麼時候的）
是兩件事。一段還在被盯的序列，每收一根新的就多一列新的 `fetched_at`；一段沒有人再
看的，最新那一列的 `fetched_at` 就停在他最後一次打開那張圖的時候。

所以刪的條件是「**整段**序列的最新 `fetched_at` 超過 N 天」——不是「fetched_at 超過
N 天的那幾列」。後者會把還在用的序列的歷史刪掉：那些舊列本來就是很久以前寫進去的。

＊ 門檻要留幾倍的餘裕。

最長的週期是 `1mo`，也就是一段還在被盯的月線序列，最多可能 31 天才多一列。90 天是
三根月線的餘裕。訂太短的代價是「上游掛掉的時候那張圖沒有底可以墊」——而那正是這些
資料存在的全部理由（`_prime_from_storage`）。
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.enums import DataSource
from app.models.market import MarketBar
from app.models.mixins import utcnow
from app.services import bar_store
from app.services.market_data.base import Bar, Timeframe

_START = datetime(2026, 3, 2, tzinfo=UTC)


def _bars(count: int, *, symbol: str, timeframe: Timeframe) -> list[Bar]:
    return [
        Bar(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=_START + timedelta(minutes=i),
            open=100.0,
            high=101.0,
            low=99.0,
            close=float(i),
            volume=1000.0,
        )
        for i in range(count)
    ]


def _store(db, symbol: str, timeframe: Timeframe, *, fetched_days_ago: float, count: int = 5):
    bar_store.save(
        db, DataSource.YFINANCE, symbol, timeframe, _bars(count, symbol=symbol, timeframe=timeframe)
    )
    db.query(MarketBar).filter(
        MarketBar.symbol == symbol, MarketBar.timeframe == timeframe.value
    ).update({MarketBar.fetched_at: utcnow() - timedelta(days=fetched_days_ago)})
    db.flush()


def _rows(db, symbol: str, timeframe: Timeframe) -> int:
    return db.execute(
        select(func.count())
        .select_from(MarketBar)
        .where(MarketBar.symbol == symbol, MarketBar.timeframe == timeframe.value)
    ).scalar_one()


def test_a_series_nobody_has_fetched_for_months_is_dropped_whole(db_session):
    """他半年前查過一次的那個代號。整段丟掉，不是丟一半。"""
    _store(db_session, "OLD.TW", Timeframe.DAY_1, fetched_days_ago=200)

    bar_store.sweep_idle_series(db_session)

    assert _rows(db_session, "OLD.TW", Timeframe.DAY_1) == 0


def test_a_series_still_being_watched_keeps_all_of_its_history(db_session):
    """**包括它很久以前的那幾根。**

    那些舊列本來就是很久以前寫進去的，所以逐列看 `fetched_at` 會把它們全部刪掉——
    而那正是這些資料存在的理由：上游掛掉的時候圖表還有底可以墊。
    """
    _store(db_session, "LIVE.TW", Timeframe.DAY_1, fetched_days_ago=0, count=5)
    # 一根很久以前就寫進來的（同一段序列裡的舊歷史）。
    db_session.add(
        MarketBar(
            data_source=DataSource.YFINANCE,
            symbol="LIVE.TW",
            timeframe=Timeframe.DAY_1.value,
            ts=_START - timedelta(days=400),
            open=1,
            high=1,
            low=1,
            close=1,
            volume=1,
            fetched_at=utcnow() - timedelta(days=400),
        )
    )
    db_session.flush()

    bar_store.sweep_idle_series(db_session)

    assert _rows(db_session, "LIVE.TW", Timeframe.DAY_1) == 6


def test_it_only_takes_the_idle_one(db_session):
    """三個條件缺一不可——這張表的每一個刪除都踩過同一個坑（見 `_trim`）。"""
    _store(db_session, "OLD.TW", Timeframe.DAY_1, fetched_days_ago=200)
    _store(db_session, "LIVE.TW", Timeframe.DAY_1, fetched_days_ago=1)
    # 同一個代號、不同週期：他還在看日線，但一年前看過一次的分線該走。
    _store(db_session, "LIVE.TW", Timeframe.MINUTE_1, fetched_days_ago=300)

    bar_store.sweep_idle_series(db_session)

    assert _rows(db_session, "OLD.TW", Timeframe.DAY_1) == 0
    assert _rows(db_session, "LIVE.TW", Timeframe.MINUTE_1) == 0
    assert _rows(db_session, "LIVE.TW", Timeframe.DAY_1) == 5


def test_the_threshold_leaves_room_for_the_slowest_timeframe(db_session):
    """一段還在被盯的月線序列，最多可能 31 天才多一列。

    門檻訂在那個數字附近的話，他每個月都會掉一次底，而症狀是上游抖一下那張圖就
    空了。
    """
    _store(db_session, "MONTHLY.TW", Timeframe.MONTH_1, fetched_days_ago=35)

    bar_store.sweep_idle_series(db_session)

    assert _rows(db_session, "MONTHLY.TW", Timeframe.MONTH_1) == 5


def test_it_runs_off_the_write_path_without_being_scheduled(db_session, monkeypatch):
    """跟 `_trim` 同一條路：寫的時候自己修剪。

    排程要有人去按、去設、去看它有沒有在跑，而這個使用者不會做那三件事的任何一件。
    """
    _store(db_session, "OLD.TW", Timeframe.DAY_1, fetched_days_ago=200)
    monkeypatch.setattr(bar_store, "_last_sweep_at", None)

    bar_store.save(
        db_session,
        DataSource.YFINANCE,
        "NEW.TW",
        Timeframe.DAY_1,
        _bars(3, symbol="NEW.TW", timeframe=Timeframe.DAY_1),
    )

    assert _rows(db_session, "OLD.TW", Timeframe.DAY_1) == 0


def test_a_sweep_that_fails_does_not_take_the_write_with_it(db_session, monkeypatch):
    """回收空間是次要的，存下這一批 K 棒是主要的。

    這條路跑在使用者正在等的那個 HTTP 請求裡，也跑在盯盤迴圈的一輪裡。
    """

    def boom(*args, **kwargs):
        raise RuntimeError("這一刻資料庫不開心")

    monkeypatch.setattr(bar_store, "_idle_series", boom)
    monkeypatch.setattr(bar_store, "_last_sweep_at", None)

    bar_store.save(
        db_session,
        DataSource.YFINANCE,
        "NEW.TW",
        Timeframe.DAY_1,
        _bars(3, symbol="NEW.TW", timeframe=Timeframe.DAY_1),
    )

    assert _rows(db_session, "NEW.TW", Timeframe.DAY_1) == 3
