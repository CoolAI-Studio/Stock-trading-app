"""要 2000 根日線，就不能只問五年然後把差額吞掉。

MEASURED: `backtest.py` 算出一個最大到 `MAX_HISTORY_FETCH_BARS`（20,000）的
`depth` 傳給 `get_bars(limit=...)`，而 `yfinance_provider.get_bars` **完全忽略
那個 limit**：它一律用 `_PERIOD_FOR[timeframe]` 的固定區間去要，然後
`frame.tail(limit)` 裁掉。日線那一格是 `"5y"`。

後果有兩種，兩種都不會有人發現：

    起點早於約 2021 年的日線回測被靜默截斷——結果是真的，但測的不是他要的區間。
    整段都在那之前的回測回傳零根，然後被歸咎成「可能是代號打錯」。

這正是 `service.py` 當初花了力氣防的那件事（「一個較短的快取不能回答一個更深的
問題」），只是同一個錯在下一層又發生一次。所以修法必須讓區間成為 limit 的函數，
而不是換一個比較大的常數——常數只是把同一天往後推。
"""

import pytest

from app.services.market_data.base import BarFetchError, Timeframe
from app.services.market_data.providers.yfinance_provider import _period_days


def test_asking_for_more_candles_asks_yahoo_for_more_history():
    """核心的不變式：區間是 limit 的函數。"""
    shallow = _period_days(Timeframe.DAY_1, 300)
    deep = _period_days(Timeframe.DAY_1, 3000)

    assert deep > shallow


def test_two_thousand_daily_candles_reaches_back_far_enough():
    """252 個交易日一年，所以 2000 根日線大約是八年——而原本問的是五年。"""
    days = _period_days(Timeframe.DAY_1, 2000)

    assert days >= 2000 * 1.4


def test_a_ten_year_weekly_backtest_is_not_capped_at_ten_years_either():
    """週線原本固定 10y，也就是大約 520 根。要 1000 根就得問更久。"""
    days = _period_days(Timeframe.WEEK_1, 1000)

    assert days >= 1000 * 7


def test_monthly_no_longer_relies_on_the_word_max():
    """MEASURED: `range=max&interval=1mo` 對 AAPL 只回 168 根，比
    DEFAULT_BAR_LIMIT(300) 還少——yfinance 的 max 分支只認得一份固定的 interval
    清單。所以月線今天就是短的，不是「起點很早才會遇到」。"""
    days = _period_days(Timeframe.MONTH_1, 300)

    assert days >= 300 * 28


@pytest.mark.parametrize(
    ("timeframe", "cap_days"),
    [
        (Timeframe.MINUTE_1, 7),
        (Timeframe.MINUTE_5, 60),
        (Timeframe.MINUTE_15, 60),
        (Timeframe.MINUTE_30, 60),
        (Timeframe.HOUR_1, 730),
        (Timeframe.HOUR_4, 730),
    ],
)
def test_intraday_still_stops_at_the_wall_yahoo_actually_has(timeframe, cap_days):
    """盤中的那些不是偏好問題，是硬牆：**超過上限 Yahoo 回的是空的 frame，不是
    短的**。所以要到上限為止，多要一天都會把資料變成零。"""
    assert _period_days(timeframe, 100_000) == cap_days


def test_a_small_request_still_asks_for_enough_to_warm_an_indicator():
    """一個 200 期的指標要暖身，而週末和假日不會有 K 棒。太貼著算等於把暖身
    需要的那幾根算掉。"""
    days = _period_days(Timeframe.DAY_1, 10)

    assert days >= 30


def test_the_provider_passes_that_period_to_yahoo(monkeypatch):
    """區間算對了但沒送出去，就等於沒改。"""
    from app.services.market_data.providers import yfinance_provider

    captured: dict[str, object] = {}

    class _Frame:
        empty = True

        def tail(self, n):
            return self

        def iterrows(self):
            return iter(())

    class _Ticker:
        def __init__(self, symbol: str) -> None:
            captured["symbol"] = symbol

        def history(self, **kwargs):
            captured.update(kwargs)
            return _Frame()

    monkeypatch.setattr(yfinance_provider.yf, "Ticker", _Ticker)

    # 空的 frame 會被正確地變成 BarFetchError（「問不到」不是「沒有歷史」，
    # 那是 service.py 那一層在意的區別）。這裡在意的是送出去的區間。
    with pytest.raises(BarFetchError):
        yfinance_provider.YFinanceProvider().get_bars("AAPL", Timeframe.DAY_1, 2000)

    assert captured["period"] == f"{_period_days(Timeframe.DAY_1, 2000)}d"
    assert captured["interval"] == "1d"
