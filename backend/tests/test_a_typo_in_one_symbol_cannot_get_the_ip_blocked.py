"""一個打錯的代號，不可以讓這個部署一直敲上游。

上游解不出來的代號不會拋例外，它只是**不出現在回答裡**。所以它永遠留在「還沒拿到」
那一堆，而 `get_quotes` 的補抓那一條看到的就是「這個代號還缺著」——於是每一次 TTL
還沒過的請求，都額外送出一次只為了它的抓取。

量過（同一支測試裡的形狀）：迴圈每五秒問一次全部、前端在中間每秒問一次，20 次請求
換來 20 次上游呼叫，其中 **16 次是只為了那一個打錯的代號**。

而使用者不是工程師：打錯代號、台股少打 `.TW`，正是他最可能犯的錯——service.py 自己
的註解就列著這兩種。一個打錯的字換來持續敲上游，接下來就是 429 或整個 IP 被擋，而那
一刻**每一個代號**都抓不到，不只那個打錯的。警告全面停擺，起因是一個錯字。

修法不是「放棄那個代號」：完整刷新那條路（TTL 過了就問全部）本來就會再問它一次，所
以它自己會回來。要拿掉的只是 TTL 內那些額外的、只為了它的補抓。
"""

from decimal import Decimal

from app.enums import DataSource
from app.services.market_data.base import Quote
from app.services.market_data.service import FAILED_FETCH_RETRY_SEC, MarketDataService


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _Provider:
    """解得出來的就回，解不出來的就**不出現在回答裡**——這是真的上游的行為。"""

    data_source = DataSource.YFINANCE

    def __init__(self, unresolvable: set[str]) -> None:
        self.unresolvable = set(unresolvable)
        self.calls: list[list[str]] = []

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        self.calls.append(list(symbols))
        return {
            s: Quote(symbol=s, data_source=self.data_source, price=Decimal(1))
            for s in symbols
            if s not in self.unresolvable
        }

    def asked_for(self, symbol: str) -> int:
        return sum(1 for call in self.calls if symbol in call)


def _service(provider: _Provider, clock: _Clock) -> MarketDataService:
    return MarketDataService(providers={DataSource.YFINANCE: provider}, clock=clock)


def _a_day_of_polling(service: MarketDataService, clock: _Clock, symbols: list[str]) -> None:
    """迴圈每五秒問一次全部，前端在那五秒中間每秒問一次。"""
    for _ in range(4):
        clock.advance(5.0)
        service.get_quotes(symbols, DataSource.YFINANCE)
        for _ in range(4):
            clock.advance(1.0)
            service.get_quotes(symbols, DataSource.YFINANCE)


def test_a_symbol_that_never_resolves_is_not_asked_on_every_single_request():
    clock = _Clock()
    provider = _Provider(unresolvable={"TYPO"})

    _a_day_of_polling(_service(provider, clock), clock, ["AAPL", "TYPO"])

    assert provider.asked_for("TYPO") <= provider.asked_for("AAPL"), (
        f"打錯的那個代號被問了 {provider.asked_for('TYPO')} 次，而正常的那個只有 "
        f"{provider.asked_for('AAPL')} 次——多出來的每一次都是只為了它送出去的，"
        "而那條路的盡頭是 429。"
    )


def test_the_good_symbols_still_get_their_prices():
    """省下來的請求不可以是從正常代號身上省的。"""
    clock = _Clock()
    provider = _Provider(unresolvable={"TYPO"})
    service = _service(provider, clock)

    _a_day_of_polling(service, clock, ["AAPL", "TYPO"])
    quotes = service.get_quotes(["AAPL", "TYPO"], DataSource.YFINANCE)

    assert "AAPL" in quotes
    assert "TYPO" not in quotes


def test_a_new_symbol_added_between_refreshes_is_fetched_right_away():
    """補抓那條路本來的用途要留著。

    他在儀表板上加了一個新代號，不可以要等到下一次完整刷新才看得到價格——這正是
    `missing` 那條路存在的理由，而退避不可以順手把它一起關掉。
    """
    clock = _Clock()
    provider = _Provider(unresolvable=set())
    service = _service(provider, clock)

    service.get_quotes(["AAPL"], DataSource.YFINANCE)
    clock.advance(1.0)  # TTL 還沒過
    quotes = service.get_quotes(["AAPL", "2330.TW"], DataSource.YFINANCE)

    assert "2330.TW" in quotes, "新加的代號要立刻抓，不是等下一次完整刷新"


def test_a_symbol_that_starts_working_again_comes_back():
    """代號會自己好——上市了、改名了、或者他把 .TW 補上去了。

    退避不是放棄：完整刷新那條路每次都會再問它一次，所以「不再額外補抓」不等於
    「這個代號從此死掉」。
    """
    clock = _Clock()
    provider = _Provider(unresolvable={"2330"})
    service = _service(provider, clock)

    _a_day_of_polling(service, clock, ["AAPL", "2330"])
    provider.unresolvable.clear()
    clock.advance(FAILED_FETCH_RETRY_SEC + 1)

    quotes = service.get_quotes(["AAPL", "2330"], DataSource.YFINANCE)

    assert "2330" in quotes, "代號恢復了卻再也拿不到價格——那是把退避做成了放棄"
