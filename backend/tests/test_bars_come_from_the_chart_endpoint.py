"""行情改成直接打 Yahoo 的 chart 端點，帶瀏覽器的 User-Agent。

MEASURED（診斷時從機房 IP 量到的，不是推測）：同一時刻

    getcrumb                        → 429
    /v8/finance/chart/AAPL + Chrome UA → 200，而且附著真實資料

**擋住的是 HTTP 標頭，不是 IP。** yfinance 需要先做 crumb 交握，而那一步正是被
擋掉的那一步——所以一個免費方案的部署會在共用 IP 上整批抓不到報價，而抓不到
報價的意思是：該送的提醒沒有送。

另外量到 `get_quotes(['AAPL'])` 會發 **3 個**請求，`get_bars` 只發 1 個。所以
「報價比較便宜」的直覺是反的：報價才是主要成本，而兩邊其實可以共用同一個回應。

THE THING MOST LIKELY TO BREAK QUIETLY, and the reason it has its own test
below: 日線的時間戳記。yfinance 給的是交易所當地的午夜，chart 端點給的是那一節
的開盤時刻——對 AAPL 差 13.5 小時。而 `bar_end()` 對日線是「時間戳 ＋ 一天」，
所以照抄過來會讓每一根日線收盤棒晚十幾個小時才被放行，也就是「收盤提醒」晚了
半天。沒有人會看出來，因為每一個畫面都還是正常的。
"""

from datetime import UTC, datetime

import pytest

from app.services.market_data.base import BarFetchError, Timeframe
from app.services.market_data.providers import yfinance_provider
from app.services.market_data.providers.yfinance_provider import YFinanceProvider

# 一天的 AAPL 日線，時間戳是 09:30 New York（= 13:30 UTC，夏令時間）。
# gmtoffset 是 -4 小時。
_NY_OFFSET = -4 * 3600
_OPEN_UTC = int(datetime(2026, 8, 20, 13, 30, tzinfo=UTC).timestamp())


def _chart_payload(
    *,
    timestamps=None,
    opens=None,
    highs=None,
    lows=None,
    closes=None,
    volumes=None,
    adjclose=None,
    meta_extra=None,
) -> dict:
    meta = {
        "currency": "USD",
        "symbol": "AAPL",
        "gmtoffset": _NY_OFFSET,
        "exchangeTimezoneName": "America/New_York",
        "regularMarketPrice": 226.0,
        "chartPreviousClose": 224.0,
        "regularMarketTime": _OPEN_UTC + 6 * 3600,
    }
    meta.update(meta_extra or {})
    quote = {
        "open": opens if opens is not None else [224.0],
        "high": highs if highs is not None else [227.0],
        "low": lows if lows is not None else [223.0],
        "close": closes if closes is not None else [226.0],
        "volume": volumes if volumes is not None else [1_000_000],
    }
    indicators = {"quote": [quote]}
    if adjclose is not None:
        indicators["adjclose"] = [{"adjclose": adjclose}]
    return {
        "chart": {
            "result": [
                {
                    "meta": meta,
                    "timestamp": timestamps if timestamps is not None else [_OPEN_UTC],
                    "indicators": indicators,
                }
            ],
            "error": None,
        }
    }


class _Response:
    def __init__(self, payload, status_code=200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


@pytest.fixture
def chart(monkeypatch):
    """Captures what was sent, serves what the test says."""
    sent: dict = {"calls": []}

    def _get(url, **kwargs):
        sent["calls"].append({"url": url, **kwargs})
        return sent.get("response") or _Response(_chart_payload())

    monkeypatch.setattr(yfinance_provider.httpx, "get", _get)
    return sent


@pytest.fixture
def no_yfinance(monkeypatch):
    """yfinance 不准被碰到——這一組測試的重點就是不再走那條路。"""

    def _boom(*args, **kwargs):
        raise AssertionError("yfinance 不該被呼叫")

    monkeypatch.setattr(yfinance_provider.yf, "Ticker", _boom)


# --- 請求本身 -------------------------------------------------------------------------


def test_bars_are_asked_of_the_chart_endpoint(chart, no_yfinance):
    YFinanceProvider().get_bars("AAPL", Timeframe.DAY_1, 300)

    assert chart["calls"], "沒有發出任何請求"
    assert "v8/finance/chart/AAPL" in chart["calls"][0]["url"]


def test_with_a_browser_user_agent_because_that_is_what_was_blocked(chart, no_yfinance):
    """擋住的是標頭不是 IP：同一時刻 getcrumb 回 429，這條路帶著 Chrome 的 UA
    回 200。"""
    YFinanceProvider().get_bars("AAPL", Timeframe.DAY_1, 300)

    headers = chart["calls"][0]["headers"]
    assert "Mozilla/5.0" in headers["User-Agent"]
    assert "Chrome" in headers["User-Agent"]


def test_and_an_explicit_timeout(chart, no_yfinance):
    """yfinance 的預設是 30 秒而且 retries=0。這個迴圈每五秒跑一次，一個掛住的
    請求會拖著整輪的停損掃描和待重送通知一起等。"""
    YFinanceProvider().get_bars("AAPL", Timeframe.DAY_1, 300)

    assert chart["calls"][0]["timeout"] is not None
    assert chart["calls"][0]["timeout"] <= 20


def test_the_range_is_still_a_function_of_how_much_was_asked_for(chart, no_yfinance):
    """#36 修好的那件事不可以在換來源的時候被弄丟。"""
    YFinanceProvider().get_bars("AAPL", Timeframe.DAY_1, 2000)

    params = chart["calls"][0]["params"]
    assert params["interval"] == "1d"
    assert params["range"] == f"{yfinance_provider._period_days(Timeframe.DAY_1, 2000)}d"


# --- 解析 -----------------------------------------------------------------------------


def test_the_candle_comes_back_with_its_prices(chart, no_yfinance):
    bars = YFinanceProvider().get_bars("AAPL", Timeframe.DAY_1, 10)

    assert len(bars) == 1
    assert bars[0].open == 224.0
    assert bars[0].close == 226.0
    assert bars[0].volume == 1_000_000


def test_prices_are_adjusted_the_same_way_auto_adjust_did(chart, no_yfinance):
    """實測 `open × (adjclose/close)` 對得上 yfinance auto_adjust=True 到小數
    第四位。不調整的話，除權息那天的價格會跟回測與圖表對不起來。"""
    chart["response"] = _Response(
        _chart_payload(opens=[100.0], highs=[110.0], lows=[90.0], closes=[100.0], adjclose=[50.0])
    )

    bars = YFinanceProvider().get_bars("AAPL", Timeframe.DAY_1, 10)

    assert bars[0].close == pytest.approx(50.0)
    assert bars[0].open == pytest.approx(50.0)
    assert bars[0].high == pytest.approx(55.0)


def test_rows_yahoo_padded_with_nulls_are_dropped(chart, no_yfinance):
    """Yahoo 用 null 補洞（停牌、它自己搞錯的假日）。一個 null 進到指標裡，
    會把它之後每一個值都毒掉。"""
    chart["response"] = _Response(
        _chart_payload(
            timestamps=[_OPEN_UTC, _OPEN_UTC + 86400],
            opens=[224.0, None],
            highs=[227.0, None],
            lows=[223.0, None],
            closes=[226.0, None],
            volumes=[1_000_000, None],
        )
    )

    bars = YFinanceProvider().get_bars("AAPL", Timeframe.DAY_1, 10)

    assert len(bars) == 1


def test_a_daily_candle_is_stamped_at_local_midnight_like_before(chart, no_yfinance):
    """THE QUIET ONE. yfinance 的日線索引是交易所當地的午夜；chart 端點給的是
    開盤時刻（AAPL 差 13.5 小時）。而 `bar_end()` 對日線是「時間戳 ＋ 一天」，
    所以照抄過來會讓每一根收盤棒晚十幾個小時才被放行——收盤提醒晚半天，而畫面上
    完全看不出來。"""
    bars = YFinanceProvider().get_bars("AAPL", Timeframe.DAY_1, 10)

    # 2026-08-20 00:00 New York == 04:00 UTC
    assert bars[0].timestamp == datetime(2026, 8, 20, 4, 0, tzinfo=UTC)


def test_an_intraday_candle_keeps_the_instant_it_opened(chart, no_yfinance):
    """盤中相反：那個時刻就是這根 K 棒真正的開始，而 4 小時線的 session 對齊
    是靠它算的。"""
    bars = YFinanceProvider().get_bars("AAPL", Timeframe.HOUR_1, 10)

    assert bars[0].timestamp == datetime(2026, 8, 20, 13, 30, tzinfo=UTC)


def test_the_limit_is_still_honoured(chart, no_yfinance):
    chart["response"] = _Response(
        _chart_payload(
            timestamps=[_OPEN_UTC + i * 86400 for i in range(5)],
            opens=[100.0] * 5,
            highs=[100.0] * 5,
            lows=[100.0] * 5,
            closes=[100.0] * 5,
            volumes=[1] * 5,
        )
    )

    bars = YFinanceProvider().get_bars("AAPL", Timeframe.DAY_1, 2)

    assert len(bars) == 2


# --- 退路 -----------------------------------------------------------------------------


def test_an_unexpected_shape_falls_back_to_yfinance(chart, monkeypatch):
    """Yahoo 改過回應格式不只一次。看不懂就退回舊路，而不是把「今天沒有資料」
    當成事實存進快取十五分鐘。"""
    chart["response"] = _Response({"chart": {"result": None, "error": "whatever"}})
    called: list[str] = []

    class _Ticker:
        def __init__(self, symbol):
            called.append(symbol)

        def history(self, **kwargs):
            import pandas as pd

            return pd.DataFrame(
                {"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1]},
                index=[datetime(2026, 8, 20, tzinfo=UTC)],
            )

    monkeypatch.setattr(yfinance_provider.yf, "Ticker", _Ticker)

    bars = YFinanceProvider().get_bars("AAPL", Timeframe.DAY_1, 10)

    assert called == ["AAPL"]
    assert len(bars) == 1


def test_a_rate_limited_chart_call_also_falls_back(chart, monkeypatch):
    chart["response"] = _Response({}, status_code=429)
    called: list[str] = []

    class _Ticker:
        def __init__(self, symbol):
            called.append(symbol)

        def history(self, **kwargs):
            import pandas as pd

            return pd.DataFrame()

    monkeypatch.setattr(yfinance_provider.yf, "Ticker", _Ticker)

    # 舊路也拿不到東西時，契約不變：這是「問不到」，不是「沒有歷史」。
    with pytest.raises(BarFetchError):
        YFinanceProvider().get_bars("AAPL", Timeframe.DAY_1, 10)

    assert called == ["AAPL"]


# --- 報價走同一個回應 -----------------------------------------------------------------


def test_a_quote_comes_from_the_same_endpoint(chart, no_yfinance):
    quotes = YFinanceProvider().get_quotes(["AAPL"])

    assert "AAPL" in quotes
    assert quotes["AAPL"].price == pytest.approx(226.0)
    assert quotes["AAPL"].currency == "USD"


def test_it_finally_knows_when_the_price_was(chart, no_yfinance):
    """`quote_time` 一直被迫留 None：fast_info 完全沒有時間欄位，而用自己的時鐘
    填會讓每一個價格看起來都是現在的——包括一支下市多年的股票的最後收盤。
    chart 的 meta 有 regularMarketTime。"""
    quotes = YFinanceProvider().get_quotes(["AAPL"])

    assert quotes["AAPL"].quote_time is not None


def test_one_symbol_costs_one_request(chart, no_yfinance):
    """MEASURED: 舊的 fast_info 路徑對一個代號會發三個請求，而這個迴圈每五秒
    跑一次。共用 IP 的 429 就是這樣來的。"""
    YFinanceProvider().get_quotes(["AAPL"])

    assert len(chart["calls"]) == 1


def test_one_bad_symbol_does_not_take_the_others_down(chart, no_yfinance):
    def _get(url, **kwargs):
        chart["calls"].append({"url": url, **kwargs})
        if "BAD" in url:
            raise RuntimeError("boom")
        return _Response(_chart_payload())

    chart["calls"] = []
    import app.services.market_data.providers.yfinance_provider as module

    module.httpx.get = _get  # type: ignore[assignment]

    quotes = YFinanceProvider().get_quotes(["BAD", "AAPL"])

    assert "AAPL" in quotes
    assert "BAD" not in quotes
