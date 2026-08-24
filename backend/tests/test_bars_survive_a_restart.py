"""K 棒要活過重開機，而且不可以在分割那天長出一個假缺口。

報價有自己的表，所以重開機之後價格還在——**K 棒一根都沒存**。Render 免費方案閒置
就休眠，於是每次醒來都得重新跟 Yahoo 要一次，要不到就是一張空圖。那個不對稱就是
「圖表在線上很不可靠」的結構原因。

存下來之後有兩個陷阱，兩個都會安靜地錯：

**一、存下來的不可以壓住即時重試。** 存量若被當成新鮮的快取，圖表就會一直畫昨天
的資料而不去問今天的。所以讀進來的存量要標成「不新鮮」——它只是在抓不到的時候有
東西可以畫，不是抓得到時的替代品。

**二、分割。** provider 回的是還原價（auto_adjust），所以一次分割會讓 Yahoo 把整段
歷史改寫。只增不改的存法會在分割那天接出一個假缺口——圖上是一根不存在的長黑，而
策略會照著它算。要比對重疊區間最舊那一根的收盤價，變了就整段重寫。
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.models.enums import DataSource
from app.services import bar_store
from app.services.market_data.base import Bar, BarFetchError, Timeframe
from app.services.market_data.service import MarketDataService

_START = datetime(2026, 3, 2, tzinfo=UTC)


def _bars(count: int, *, first_close: float = 100.0, symbol: str = "AAPL") -> list[Bar]:
    return [
        Bar(
            symbol=symbol,
            timeframe=Timeframe.DAY_1,
            timestamp=_START + timedelta(days=i),
            open=first_close + i,
            high=first_close + i + 2,
            low=first_close + i - 1,
            close=first_close + i,
            volume=1000.0 + i,
        )
        for i in range(count)
    ]


def test_what_was_saved_comes_back(db_session):
    bar_store.save(db_session, DataSource.YFINANCE, "AAPL", Timeframe.DAY_1, _bars(5))

    loaded = bar_store.load(db_session, DataSource.YFINANCE, "AAPL", Timeframe.DAY_1, limit=10)

    assert [b.timestamp for b in loaded] == [b.timestamp for b in _bars(5)]
    assert [float(b.close) for b in loaded] == [100.0, 101.0, 102.0, 103.0, 104.0]


def test_the_newest_are_kept_when_more_are_asked_for_than_exist(db_session):
    bar_store.save(db_session, DataSource.YFINANCE, "AAPL", Timeframe.DAY_1, _bars(10))

    loaded = bar_store.load(db_session, DataSource.YFINANCE, "AAPL", Timeframe.DAY_1, limit=3)

    assert len(loaded) == 3
    assert loaded[-1].timestamp == _START + timedelta(days=9)


def test_saving_twice_does_not_duplicate(db_session):
    bar_store.save(db_session, DataSource.YFINANCE, "AAPL", Timeframe.DAY_1, _bars(5))
    bar_store.save(db_session, DataSource.YFINANCE, "AAPL", Timeframe.DAY_1, _bars(5))

    assert len(bar_store.load(db_session, DataSource.YFINANCE, "AAPL", Timeframe.DAY_1, 99)) == 5


def test_two_symbols_do_not_mix(db_session):
    bar_store.save(db_session, DataSource.YFINANCE, "AAPL", Timeframe.DAY_1, _bars(3))
    bar_store.save(
        db_session, DataSource.YFINANCE, "MSFT", Timeframe.DAY_1, _bars(3, symbol="MSFT")
    )

    assert len(bar_store.load(db_session, DataSource.YFINANCE, "AAPL", Timeframe.DAY_1, 99)) == 3


def test_two_timeframes_do_not_mix(db_session):
    bar_store.save(db_session, DataSource.YFINANCE, "AAPL", Timeframe.DAY_1, _bars(3))
    weekly = [
        Bar(
            symbol="AAPL",
            timeframe=Timeframe.WEEK_1,
            timestamp=_START + timedelta(weeks=i),
            open=200.0,
            high=201.0,
            low=199.0,
            close=200.0,
            volume=1.0,
        )
        for i in range(4)
    ]
    bar_store.save(db_session, DataSource.YFINANCE, "AAPL", Timeframe.WEEK_1, weekly)

    assert len(bar_store.load(db_session, DataSource.YFINANCE, "AAPL", Timeframe.DAY_1, 99)) == 3
    assert len(bar_store.load(db_session, DataSource.YFINANCE, "AAPL", Timeframe.WEEK_1, 99)) == 4


def test_a_split_rewrites_the_whole_history_instead_of_leaving_a_gap(db_session):
    """還原價被改寫的時候，只增不改會在分割那天接出一根不存在的長黑。"""
    bar_store.save(db_session, DataSource.YFINANCE, "AAPL", Timeframe.DAY_1, _bars(5))

    # 一次 1:2 分割：同一批日期，每一個價格都變成一半。
    after_split = _bars(5, first_close=50.0)
    bar_store.save(db_session, DataSource.YFINANCE, "AAPL", Timeframe.DAY_1, after_split)

    loaded = bar_store.load(db_session, DataSource.YFINANCE, "AAPL", Timeframe.DAY_1, 99)
    assert len(loaded) == 5, "重疊的日期不該變成兩份"
    assert [float(b.close) for b in loaded] == [50.0, 51.0, 52.0, 53.0, 54.0], (
        "分割之後整段歷史都要換成新的還原價，不然圖上會多一根不存在的長黑"
    )


def test_ordinary_new_bars_do_not_trigger_a_rewrite(db_session):
    """每天多一根是常態，不可以每次都當成分割整段重寫。"""
    bar_store.save(db_session, DataSource.YFINANCE, "AAPL", Timeframe.DAY_1, _bars(5))
    bar_store.save(db_session, DataSource.YFINANCE, "AAPL", Timeframe.DAY_1, _bars(7))

    loaded = bar_store.load(db_session, DataSource.YFINANCE, "AAPL", Timeframe.DAY_1, 99)
    assert len(loaded) == 7
    assert [float(b.close) for b in loaded][:5] == [100.0, 101.0, 102.0, 103.0, 104.0]


def test_nothing_saved_reads_as_nothing_not_as_an_error(db_session):
    assert bar_store.load(db_session, DataSource.YFINANCE, "NOPE", Timeframe.DAY_1, 10) == []


# --- 接進服務層：存下來的只在抓不到的時候頂著 --------------------------------


class _Provider:
    """可以切換成「抓得到」或「抓不到」的假上游。"""

    data_source = DataSource.YFINANCE

    def __init__(self) -> None:
        self.bars: list[Bar] = _bars(5)
        self.fails = False
        self.calls = 0

    def get_quotes(self, symbols):
        return {}

    def get_bars(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Bar]:
        self.calls += 1
        if self.fails:
            raise BarFetchError("上游掛了")
        return self.bars[-limit:]


@pytest.fixture
def provider_and_service():
    provider = _Provider()
    service = MarketDataService(providers={DataSource.YFINANCE: provider})
    return provider, service


def test_a_successful_fetch_is_written_down(db_session, provider_and_service):
    provider, service = provider_and_service

    service.get_bars("AAPL", Timeframe.DAY_1, DataSource.YFINANCE, limit=5, db=db_session)

    assert len(bar_store.load(db_session, DataSource.YFINANCE, "AAPL", Timeframe.DAY_1, 99)) == 5


def test_after_a_restart_the_chart_still_has_something(db_session, provider_and_service):
    """休眠醒來＝一個全新的行程，記憶體裡的快取是空的。"""
    provider, service = provider_and_service
    service.get_bars("AAPL", Timeframe.DAY_1, DataSource.YFINANCE, limit=5, db=db_session)

    # 新的行程，同一個資料庫，而這次上游抓不到。
    provider_after = _Provider()
    provider_after.fails = True
    after_restart = MarketDataService(providers={DataSource.YFINANCE: provider_after})

    bars = after_restart.get_bars(
        "AAPL", Timeframe.DAY_1, DataSource.YFINANCE, limit=5, db=db_session
    )

    assert len(bars) == 5, "存下來就是為了這一刻：上游不通，圖表仍然畫得出東西"


def test_stored_bars_never_stand_in_for_a_live_fetch(db_session, provider_and_service):
    """存下來的不是快取。

    把它當成新鮮的，圖表就會一直畫昨天的資料而不去問今天的——而這是一個提醒系
    統，畫著舊資料卻看起來正常，比畫不出來更糟。
    """
    provider, service = provider_and_service
    service.get_bars("AAPL", Timeframe.DAY_1, DataSource.YFINANCE, limit=5, db=db_session)

    provider_after = _Provider()
    provider_after.bars = _bars(6)
    after_restart = MarketDataService(providers={DataSource.YFINANCE: provider_after})

    bars = after_restart.get_bars(
        "AAPL", Timeframe.DAY_1, DataSource.YFINANCE, limit=6, db=db_session
    )

    assert provider_after.calls == 1, "有存量就不去問上游，圖表會停在重開機那一刻"
    assert len(bars) == 6


def test_without_a_session_nothing_changes(provider_and_service):
    """市場迴圈以外還有別的呼叫端。沒有給 db 就是照舊，不可以爆炸。"""
    provider, service = provider_and_service

    bars = service.get_bars("AAPL", Timeframe.DAY_1, DataSource.YFINANCE, limit=5)

    assert len(bars) == 5


# --- 圖上要看得出來這是存下來的 ----------------------------------------------


@pytest.fixture
def api_with_storage(db_session):
    """一個已經存過 K 棒、但上游現在抓不到的部署。"""
    from app.main import app
    from app.services.market_data.service import get_market_data_service

    provider = _Provider()
    warm = MarketDataService(providers={DataSource.YFINANCE: provider})
    warm.get_bars("AAPL", Timeframe.DAY_1, DataSource.YFINANCE, limit=5, db=db_session)

    broken = _Provider()
    broken.fails = True
    service = MarketDataService(providers={DataSource.YFINANCE: broken})
    app.dependency_overrides[get_market_data_service] = lambda: service
    try:
        yield service
    finally:
        app.dependency_overrides.pop(get_market_data_service, None)


def test_a_chart_drawn_from_storage_says_so(auth_client, api_with_storage):
    """一張永遠畫得出來的圖，會掩蓋一個已經死掉一週的資料源。

    對提醒類產品，那是最不能有的東西：畫面看起來正常，而它正在說謊。
    """
    body = auth_client.get("/api/market/bars?symbol=AAPL").json()

    assert len(body["bars"]) == 5, "存下來就是為了還畫得出東西"
    assert body["served_from"] == "stored"


def test_a_live_chart_says_live(auth_client, db_session):
    from app.main import app
    from app.services.market_data.service import get_market_data_service

    service = MarketDataService(providers={DataSource.YFINANCE: _Provider()})
    app.dependency_overrides[get_market_data_service] = lambda: service
    try:
        body = auth_client.get("/api/market/bars?symbol=AAPL").json()
    finally:
        app.dependency_overrides.pop(get_market_data_service, None)

    assert body["served_from"] == "live"


def test_fetch_failed_still_reports_a_failure_when_there_is_stored_data(
    auth_client, api_with_storage
):
    """票上的陷阱一：`not bars and …` 一旦有存量就永遠是 false。

    也就是說，加了持久化之後，「抓不到」這件事會從畫面上永遠消失。
    """
    body = auth_client.get("/api/market/bars?symbol=AAPL").json()

    assert body["fetch_failed"] is True
