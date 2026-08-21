"""Candles for the chart, from the data this app already has.

The dashboard's chart is TradingView's free embedded widget, and for a
Taiwanese symbol it answers 「此商品僅在 TradingView 上可用」 -- TradingView's own
words for 「the symbol is real, but this widget is not licensed to show its
data」. The symbol was never wrong: the header in that dialog reads TWSE:0050,
which is exactly what lib/tradingView.ts is supposed to produce. It is a data
licensing restriction, and no amount of symbol correctness reaches it.

Meanwhile this backend has the candles. MarketDataService.get_bars() fetches
OHLC from yfinance for these very symbols -- verified against the live feed for
0050.TW, 2330.TW and 6488.TWO -- and the whole backtest engine replays them.
The data was never the problem either. What was missing was an endpoint and a
renderer.

WHY AN ENDPOINT RATHER THAN A DIFFERENT EMBED: every other embed has the same
licensing question, and answering it with somebody else's permissions is how
the chart broke in the first place. This app can already price these symbols;
drawing what it already knows depends on nothing new.

THE CACHE IS THE REASON THIS IS SAFE TO EXPOSE. get_bars() is already the
rate-limited, per-symbol-per-timeframe cached path the market loop uses --
yfinance is an unofficial scraper and a chart that re-downloaded years of
candles on every page view would get the deployment's IP blocked. Going through
the service rather than the provider is what keeps that true.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.main import app
from app.models.enums import DataSource
from app.services.market_data.base import Bar, Timeframe
from app.services.market_data.service import MarketDataService, get_market_data_service

_START = datetime(2026, 1, 5, tzinfo=UTC)


class _Stub:
    """Daily candles for 0050.TW and nothing else, so a symbol the provider
    cannot resolve behaves the way yfinance does: no bars at all."""

    data_source = DataSource.YFINANCE

    def __init__(self) -> None:
        self.asked: list[tuple[str, Timeframe, int]] = []

    def get_quotes(self, symbols):
        return {}

    def get_bars(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Bar]:
        self.asked.append((symbol, timeframe, limit))
        if symbol != "0050.TW":
            return []
        step = timedelta(weeks=1) if timeframe is Timeframe.WEEK_1 else timedelta(days=1)
        return [
            Bar(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=_START + step * i,
                open=100.0 + i,
                high=102.0 + i,
                low=99.0 + i,
                close=101.0 + i,
                volume=1000.0 + i,
            )
            for i in range(30)
        ][-limit:]


@pytest.fixture
def stub():
    provider = _Stub()
    service = MarketDataService(providers={DataSource.YFINANCE: provider})
    app.dependency_overrides[get_market_data_service] = lambda: service
    try:
        yield provider
    finally:
        app.dependency_overrides.pop(get_market_data_service, None)


# --- the candles ---------------------------------------------------------------


def test_a_taiwanese_symbol_has_candles(auth_client, stub):
    """The one the embedded widget refuses to draw."""
    body = auth_client.get("/api/market/bars?symbol=0050.TW").json()

    assert body["symbol"] == "0050.TW"
    assert len(body["bars"]) == 30


def test_each_candle_carries_what_a_chart_needs(auth_client, stub):
    bar = auth_client.get("/api/market/bars?symbol=0050.TW").json()["bars"][0]

    assert set(bar) >= {"time", "open", "high", "low", "close", "volume"}


def test_the_candles_are_oldest_first(auth_client, stub):
    """The order every charting library expects, and the order the backtest
    replays them in. Reversing it draws a mirror image nobody notices."""
    times = [
        bar["time"] for bar in auth_client.get("/api/market/bars?symbol=0050.TW").json()["bars"]
    ]

    assert times == sorted(times)


def test_the_timeframe_can_be_chosen(auth_client, stub):
    auth_client.get("/api/market/bars?symbol=0050.TW&timeframe=1wk")

    assert stub.asked[-1][1] is Timeframe.WEEK_1


def test_how_many_candles_can_be_chosen(auth_client, stub):
    body = auth_client.get("/api/market/bars?symbol=0050.TW&limit=10").json()

    assert len(body["bars"]) == 10


def test_the_response_says_which_timeframe_it_answered_with(auth_client, stub):
    """A chart showing weekly candles under a 「日」 label is a wrong chart that
    looks right, which is the failure this whole area keeps producing."""
    body = auth_client.get("/api/market/bars?symbol=0050.TW&timeframe=1wk").json()

    assert body["timeframe"] == "1wk"


# --- what it refuses, and what it merely reports --------------------------------


def test_a_symbol_that_cannot_price_is_refused_before_the_network(auth_client, stub):
    """「台積電」 and a bare 「2330」 are the two shapes this app spent weeks
    learning to refuse. Asking yfinance about them is a wasted request against
    a rate limiter, and a bare 2330 would answer with a Japanese company."""
    resp = auth_client.get("/api/market/bars?symbol=台積電")

    assert resp.status_code == 422
    assert stub.asked == []


def test_a_real_symbol_with_no_history_is_not_an_error(auth_client, stub):
    """A newly listed stock genuinely has no candles yet. 500ing over it would
    make the page look broken instead of empty."""
    resp = auth_client.get("/api/market/bars?symbol=9999.TW")

    assert resp.status_code == 200
    assert resp.json()["bars"] == []


def test_an_absurd_limit_is_refused_rather_than_served(auth_client, stub):
    """A chart asking for a hundred thousand candles is a mistake, and serving
    it costs the deployment its yfinance access."""
    assert auth_client.get("/api/market/bars?symbol=0050.TW&limit=100000").status_code == 422


def test_it_needs_a_login(client):
    assert client.get("/api/market/bars?symbol=0050.TW").status_code == 401


# --- it goes through the cache, which is what keeps yfinance reachable ----------


def test_two_page_views_do_not_mean_two_downloads(auth_client, stub):
    """get_bars() is the rate-limited, per-symbol-per-timeframe cached path the
    market loop already uses. yfinance is an unofficial scraper, and a chart
    that re-downloaded years of candles on every page view would get the
    deployment's IP blocked."""
    auth_client.get("/api/market/bars?symbol=0050.TW")
    auth_client.get("/api/market/bars?symbol=0050.TW")

    assert len(stub.asked) == 1
