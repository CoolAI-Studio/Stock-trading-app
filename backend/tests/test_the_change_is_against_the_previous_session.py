"""漲跌幅要對「前一個交易日的收盤」算，不是對一週前。

MEASURED（2026-09-14 美股收盤後，同一時刻、同一檔 AAPL）：

    range=1d → meta.chartPreviousClose 332.27 ＝ 9/11 收盤
    range=5d → meta.chartPreviousClose 319.97 ＝ 9/04 收盤（區間是 9/08–9/14 五節）

`chartPreviousClose` 是「查詢區間第一根 K 棒之前那一節的收盤」。報價一直問 5d，所以
儀表板上的漲跌幅從 #37 起每一檔都在跟一週前比——NVDA 那天跌 3.36%，畫面寫 -8.42%。
沒有任何東西會變紅：一個錯的漲跌幅長得跟對的一模一樣。

所以這裡的假 Yahoo 不寫死 meta，而是照量到的語意從一串收盤切出來：問幾節給幾根，前
收盤取區間之前那一節。問 5d 會重現 319.97，問 1d 是 332.27——測的是「前收盤是前一個
交易日」這個性質，不是「參數寫著 1d」。

退路（yfinance 的 fast_info）有同一種錯，也是量到的：`previousClose` 是 332.55（它拿
一週的小時線、含盤前盤後自己算），`regularMarketPreviousClose` 才是 332.27。台積電同
一時刻 2420 對 2410。
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.services.market_data.providers import yfinance_provider
from app.services.market_data.providers.yfinance_provider import YFinanceProvider

# AAPL 那兩週真正的收盤（紐約當地日期）。9/07 是勞動節，所以 5d 的區間從 9/08 開始。
_AAPL_SESSIONS = [
    ("2026-09-04", 319.97),
    ("2026-09-08", 316.22),
    ("2026-09-09", 315.34),
    ("2026-09-10", 326.57),
    ("2026-09-11", 332.27),
    ("2026-09-14", 333.08),
]
_NY_OFFSET = -4 * 3600


class _Response:
    def __init__(self, payload, status_code=200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _yahoo_chart(sessions, chart_range: str) -> dict:
    """照實測的語意回一份 chart：`Nd` 是最後 N 節，前收盤是那 N 節之前的一節。"""
    assert chart_range.endswith("d"), f"這個假 Yahoo 只量過以天為單位的區間，被問了 {chart_range}"
    window = sessions[-int(chart_range[:-1]) :]
    before = sessions[: len(sessions) - len(window)]
    opens = [
        int(datetime.fromisoformat(day).replace(hour=13, minute=30, tzinfo=UTC).timestamp())
        for day, _ in window
    ]
    closes = [close for _, close in window]
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "currency": "USD",
                        "symbol": "AAPL",
                        "gmtoffset": _NY_OFFSET,
                        "exchangeTimezoneName": "America/New_York",
                        "regularMarketPrice": closes[-1],
                        "chartPreviousClose": before[-1][1] if before else None,
                        "regularMarketTime": opens[-1] + int(6.5 * 3600),
                    },
                    "timestamp": opens,
                    "indicators": {
                        "quote": [
                            {
                                "open": closes,
                                "high": closes,
                                "low": closes,
                                "close": closes,
                                "volume": [1] * len(closes),
                            }
                        ]
                    },
                }
            ],
            "error": None,
        }
    }


@pytest.fixture
def yahoo(monkeypatch):
    def _get(url, params=None, **kwargs):
        return _Response(_yahoo_chart(_AAPL_SESSIONS, params["range"]))

    monkeypatch.setattr(yfinance_provider.httpx, "get", _get)


def test_the_fake_says_what_yahoo_said_that_evening():
    """假的要先對得上真的，下面那一條的紅燈才是為了對的原因。"""
    meta_5d = _yahoo_chart(_AAPL_SESSIONS, "5d")["chart"]["result"][0]["meta"]
    meta_1d = _yahoo_chart(_AAPL_SESSIONS, "1d")["chart"]["result"][0]["meta"]

    assert meta_5d["chartPreviousClose"] == 319.97
    assert meta_1d["chartPreviousClose"] == 332.27
    assert meta_5d["regularMarketPrice"] == meta_1d["regularMarketPrice"] == 333.08


def test_the_previous_close_is_the_session_before_the_last_one(yahoo):
    quote = YFinanceProvider().get_quotes(["AAPL"])["AAPL"]

    assert quote.price == Decimal("333.08")
    assert quote.prev_close == Decimal("332.27"), "前收盤要是 9/11，不是一週前的 9/04"


def test_so_the_change_on_the_dashboard_is_the_days_change(yahoo):
    quote = YFinanceProvider().get_quotes(["AAPL"])["AAPL"]

    # (333.08 - 332.27) / 332.27 = +0.24%，不是對 319.97 算出來的 +4.10%
    assert float(quote.change_pct) == pytest.approx(0.2438, abs=1e-4)


def test_the_fallback_reads_the_regular_session_close_too(monkeypatch):
    """chart 端點走不通時退回 fast_info。它的 `previousClose` 是含盤前盤後的小時線
    自己算的（量到 332.55），那不是券商 App 上寫的前收盤。"""

    def _blocked(url, **kwargs):
        return _Response({}, status_code=429)

    class _FastInfo(dict):
        currency = "USD"

    class _Ticker:
        def __init__(self, symbol):
            self.fast_info = _FastInfo(
                lastPrice=333.08,
                previousClose=332.55,
                regularMarketPreviousClose=332.27,
            )

    monkeypatch.setattr(yfinance_provider.httpx, "get", _blocked)
    monkeypatch.setattr(yfinance_provider.yf, "Ticker", _Ticker)

    quote = YFinanceProvider().get_quotes(["AAPL"])["AAPL"]

    assert quote.prev_close == Decimal("332.27")
