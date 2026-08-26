"""替身在真貨會失敗的地方成功，就不是替身，是一塊遮布。

`mock_provider.py` 自己的 docstring 寫著：

    A double that succeeds where the real thing fails cannot fail a test, it
    can only hide one.

而它的 `get_bars` 不管什麼週期都給得出 K 棒。所以 `12h` 在 mock 上有資料、在
Yahoo 上沒有——任何用 mock 寫的測試都不會看到那個失敗，包括那些本來就是為了
「這個來源有沒有這個週期」而寫的。

（那道閘門這一批剛補進回測：#30。這一條是讓替身跟真貨在同一件事上失敗，
免得下一次同樣的洞在測試裡是綠的。）
"""

import pytest

from app.enums import DataSource
from app.services.market_data.base import BarFetchError, Timeframe
from app.services.market_data.providers.mock_provider import MockProvider


def _provider() -> MockProvider:
    return MockProvider()


def test_a_timeframe_this_source_does_not_serve_is_refused():
    """12 小時線只有加密貨幣有。mock 的 data_source 是 yfinance。"""
    with pytest.raises(BarFetchError):
        _provider().get_bars("2330.TW", Timeframe.HOUR_12, 100)


def test_a_timeframe_it_does_serve_still_works():
    """把替身改成什麼都拒絕，等於把用它的每一條測試變成假的。"""
    bars = _provider().get_bars("2330.TW", Timeframe.DAY_1, 10)

    assert len(bars) == 10


def test_the_refusal_is_the_same_kind_the_real_one_raises():
    """`BarFetchError` 有專門的意思：「問不到」而不是「這個代號沒有歷史」。
    service 那一層靠這個區別決定要不要把空清單當成事實存進快取。"""
    with pytest.raises(BarFetchError) as raised:
        _provider().get_bars("2330.TW", Timeframe.HOUR_12, 100)

    assert "12" in str(raised.value) or "小時" in str(raised.value)


def test_every_timeframe_the_source_claims_to_support_really_works():
    """反過來的那一半：宣告支援的每一個週期，替身都要給得出東西。"""
    from app.services.market_data.base import SUPPORTED_TIMEFRAMES

    for timeframe in SUPPORTED_TIMEFRAMES[DataSource.YFINANCE]:
        assert _provider().get_bars("2330.TW", timeframe, 5)
