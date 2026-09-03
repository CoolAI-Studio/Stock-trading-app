"""記憶體裡的 K 棒快取要有上限——它是行程裡最大的一塊，而大小由外面決定。

`MarketDataService._bar_cache` 的鍵是（來源、代號、週期），值是那一段抓回來的 K 棒。
沒有任何東西會把它拿掉：TTL 只決定「這一筆還算不算新鮮」，過期的那一筆照樣佔著記憶
體，等下一次同樣的請求把它蓋掉。沒有下一次的，就永遠躺在那裡。

而深度是使用者決定的：圖表往前拉會問到 3500 根（`MAX_CHART_BARS`）。量過一筆滿的是
**687 KB**，所以看過 100 個代號 × 5 個週期就是 344 MB——而免費方案整台是 512 MB，策
略池自己還佔 60 MB。

行程被 OOM 殺掉的意思是**每一則提醒都停了**。一份「抓不到時可以少問一次上游」的快
取，不值得為它把整個 app 停掉。

上限用**總 K 棒根數**而不是筆數：那才是真正花掉的東西。用筆數的話，同一個數字對盯盤
迴圈（一筆 300 根）和對深拉的圖表（一筆 3500 根）差了十倍。
"""

from decimal import Decimal

from app.enums import DataSource
from app.services.market_data.base import Bar, Quote, Timeframe
from app.services.market_data.service import MAX_CACHED_BARS, MarketDataService


class _Provider:
    """要幾根給幾根，並且記下被問過幾次。"""

    data_source = DataSource.YFINANCE

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        return {s: Quote(symbol=s, data_source=self.data_source, price=Decimal(1)) for s in symbols}

    def get_bars(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Bar]:
        self.calls.append(symbol)
        from datetime import UTC, datetime, timedelta

        start = datetime(2026, 1, 1, tzinfo=UTC)
        return [
            Bar(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=start + timedelta(days=i),
                open=1.0,
                high=1.0,
                low=1.0,
                close=1.0,
                volume=1.0,
            )
            for i in range(limit)
        ]


def _service(provider: _Provider) -> MarketDataService:
    return MarketDataService(providers={DataSource.YFINANCE: provider})


def test_the_cache_does_not_grow_without_a_ceiling():
    """看過幾百個代號撐不大這個行程。"""
    service = _service(_Provider())
    per_symbol = 1_000
    for i in range(MAX_CACHED_BARS // per_symbol * 3):
        service.get_bars(f"SYM{i}", Timeframe.DAY_1, DataSource.YFINANCE, limit=per_symbol)

    assert service.cached_bar_count() <= MAX_CACHED_BARS


def test_the_one_just_fetched_is_never_the_one_thrown_away():
    """剛抓回來的那一筆不可以被自己觸發的淘汰丟掉。

    丟掉的話，同一個請求連問兩次就會打上游兩次——快取變成負擔而不是幫忙，而且那正好
    發生在快取滿了、也就是最忙的時候。
    """
    provider = _Provider()
    service = _service(provider)
    per_symbol = 1_000
    for i in range(MAX_CACHED_BARS // per_symbol * 3):
        service.get_bars(f"SYM{i}", Timeframe.DAY_1, DataSource.YFINANCE, limit=per_symbol)

    last = f"SYM{MAX_CACHED_BARS // per_symbol * 3 - 1}"
    before = provider.calls.count(last)
    service.get_bars(last, Timeframe.DAY_1, DataSource.YFINANCE, limit=per_symbol)

    assert provider.calls.count(last) == before, "剛抓回來的那一筆被自己的淘汰丟掉了"


def test_a_single_series_deeper_than_the_whole_budget_is_still_served():
    """一筆自己就超過預算的，留著。

    丟掉它換不到任何東西——下一次同樣的請求還是會把它抓回來，而且那一次的答案還是得
    在記憶體裡存在過。上限的意思是「不要累積」，不是「不准回答」。
    """
    provider = _Provider()
    service = _service(provider)

    bars = service.get_bars(
        "HUGE", Timeframe.DAY_1, DataSource.YFINANCE, limit=MAX_CACHED_BARS + 500
    )

    # 不是剛好 +500：`closed_bars` 會把最後那根還沒收完的丟掉。這裡要的是「這一筆自己
    # 就比整個預算大」，不是精確的根數。
    assert len(bars) > MAX_CACHED_BARS
    before = len(provider.calls)
    service.get_bars("HUGE", Timeframe.DAY_1, DataSource.YFINANCE, limit=MAX_CACHED_BARS + 500)
    assert len(provider.calls) == before, "自己就超過預算的那一筆被丟掉了，於是每次都重抓"
