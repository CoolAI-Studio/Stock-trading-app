"""「查不到 AAPL 的歷史資料」 for a symbol that has fifty years of it.

Reported from the deployed app: the chart says AAPL has no history, while the
same page lists AAPL at US$311.30. Locally the provider returns 250 bars for
AAPL and so does the service, so it only happens on the deployed box.

WHAT ACTUALLY HAPPENS, and it is two bugs stacked:

  The provider cannot fail. Every path out of `get_bars` returns `[]` --
  `except Exception: return []`, and `if frame is None or frame.empty: return
  []`. yfinance 1.6.0 ships `hide_exceptions = True`, so `history()` swallows
  its OWN errors and hands back an empty frame; only YFRateLimitError escapes,
  and our bare `except` eats that. 「the request failed」 and 「this symbol has
  no history」 arrive as the same value.

  The service then caches that value as an answer. `self._bar_cache[key] =
  (now, limit, bars)` runs unconditionally, so one transient failure is served
  as fact for the whole TTL -- 900 seconds for a daily chart.

It is a COLD-CACHE failure, which is why it never reproduces locally: `bars =
fetched or cached` keeps prior history, so only the FIRST fetch for a given
(source, symbol, timeframe) can poison the entry. On Render's free tier the
process spins down when idle, so every wake gets exactly one chance -- and the
market loop has already spent that IP's budget on its quote burst before the
chart asks.

WHAT MUST NOT BE LOST. The current behaviour exists for a real reason, written
in its own comment: a symbol the provider genuinely cannot resolve must not be
re-requested on every single poll, 「which is exactly how an IP gets blocked」.
That protection stays. What changes is that it now applies to an ANSWER of
「there is nothing here」, and not to a failure to ask.
"""

import pytest

from app.enums import DataSource
from app.services.market_data.base import Bar, BarFetchError, Timeframe
from app.services.market_data.service import MarketDataService


def _bars(symbol: str, timeframe: Timeframe, count: int = 5) -> list[Bar]:
    from datetime import UTC, datetime, timedelta

    start = datetime(2026, 1, 5, tzinfo=UTC)
    return [
        Bar(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=start + timedelta(days=i),
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
            volume=1000.0,
        )
        for i in range(count)
    ]


class _Feed:
    """A provider whose next answer is scripted: bars, nothing, or a failure."""

    data_source = DataSource.YFINANCE

    def __init__(self, script) -> None:
        self.script = script
        self.calls = 0

    def get_quotes(self, symbols):
        return {}

    def get_bars(self, symbol, timeframe, limit):
        self.calls += 1
        answer = self.script(self.calls) if callable(self.script) else self.script
        if isinstance(answer, Exception):
            raise answer
        return answer


def _service(feed: _Feed, clock: list[float]) -> MarketDataService:
    return MarketDataService(providers={DataSource.YFINANCE: feed}, clock=lambda: clock[0])


# --- a failure is not an answer -------------------------------------------------


def test_a_failed_fetch_is_not_served_as_no_history_for_fifteen_minutes():
    """The reported bug, at the service boundary. A symbol with fifty years of
    history read as having none, for the whole of a daily chart's TTL."""
    clock = [0.0]
    feed = _Feed(
        lambda n: BarFetchError("rate limited") if n == 1 else _bars("AAPL", Timeframe.DAY_1)
    )
    service = _service(feed, clock)

    first = service.get_bars("AAPL", Timeframe.DAY_1, DataSource.YFINANCE, limit=250)
    clock[0] = 90.0
    second = service.get_bars("AAPL", Timeframe.DAY_1, DataSource.YFINANCE, limit=250)

    assert first == []
    assert second, "a failure was cached as if it were the answer"


def test_but_it_is_not_retried_on_every_single_request_either():
    """The protection the current code was written for, and it stays. A page
    that refetches on focus must not turn one 429 into a hundred."""
    clock = [0.0]
    feed = _Feed(BarFetchError("rate limited"))
    service = _service(feed, clock)

    for _ in range(10):
        clock[0] += 1.0
        service.get_bars("AAPL", Timeframe.DAY_1, DataSource.YFINANCE, limit=250)

    assert feed.calls == 1


def test_the_pause_after_a_failure_is_far_shorter_than_the_normal_ttl():
    """A transient 429 should cost a minute, not the fifteen a real answer is
    cached for. Somebody refreshing the page has to be able to get out of it."""
    from app.services.market_data.service import _DEFAULT_BAR_TTL_SEC, FAILED_FETCH_RETRY_SEC

    assert FAILED_FETCH_RETRY_SEC < _DEFAULT_BAR_TTL_SEC[Timeframe.DAY_1] / 5


def test_a_genuinely_empty_answer_is_still_cached_for_the_full_ttl():
    """The case the original comment is about: a symbol the provider resolves
    and has nothing for. Re-asking every poll is how an IP gets blocked."""
    clock = [0.0]
    feed = _Feed([])
    service = _service(feed, clock)

    for _ in range(10):
        clock[0] += 5.0
        assert service.get_bars("NOPE.TW", Timeframe.DAY_1, DataSource.YFINANCE, limit=250) == []

    assert feed.calls == 1


def test_history_already_held_survives_a_failure():
    """One failed request must not look like 「this strategy has no history
    yet」 and silently restart a warm-up."""
    clock = [0.0]
    feed = _Feed(lambda n: _bars("AAPL", Timeframe.DAY_1) if n == 1 else BarFetchError("boom"))
    service = _service(feed, clock)

    service.get_bars("AAPL", Timeframe.DAY_1, DataSource.YFINANCE, limit=250)
    clock[0] = 10_000.0

    assert service.get_bars("AAPL", Timeframe.DAY_1, DataSource.YFINANCE, limit=250)


def test_the_failure_is_logged_by_name(caplog):
    """A chart that says 「could not fetch」 and a log that says nothing leaves
    nobody able to tell a rate limit from a delisting."""
    import logging

    service = _service(_Feed(BarFetchError("429 Too Many Requests")), [0.0])

    with caplog.at_level(logging.WARNING):
        service.get_bars("AAPL", Timeframe.DAY_1, DataSource.YFINANCE, limit=250)

    assert any("AAPL" in record.getMessage() for record in caplog.records)


# --- the provider has to be able to say it failed ---------------------------------


def test_a_rate_limit_from_yfinance_is_a_failure_not_an_absence(monkeypatch):
    """The one error yfinance re-raises rather than hiding, and the one most
    likely on a shared deployment IP."""
    from app.services.market_data.providers import yfinance_provider

    class _Boom:
        def __init__(self, symbol):
            pass

        def history(self, **kwargs):
            raise RuntimeError("Too Many Requests. Rate limited.")

    monkeypatch.setattr(yfinance_provider.yf, "Ticker", _Boom)

    with pytest.raises(BarFetchError):
        yfinance_provider.YFinanceProvider().get_bars("AAPL", Timeframe.DAY_1, 250)


def test_an_empty_frame_is_reported_as_a_failure_not_as_no_history(monkeypatch):
    """yfinance 1.6.0 ships hide_exceptions=True, so history() swallows its own
    errors and returns an EMPTY FRAME rather than raising. That is the likelier
    path of the two, and treating it as 「this symbol has no history」 is what
    poisons the cache."""
    from app.services.market_data.providers import yfinance_provider

    class _Empty:
        def __init__(self, symbol):
            pass

        def history(self, **kwargs):
            import pandas as pd

            return pd.DataFrame()

    monkeypatch.setattr(yfinance_provider.yf, "Ticker", _Empty)

    with pytest.raises(BarFetchError):
        yfinance_provider.YFinanceProvider().get_bars("AAPL", Timeframe.DAY_1, 250)


def test_a_crypto_fetch_that_fails_says_so_too(monkeypatch):
    """The same disease would produce the same silence on the Binance side."""
    from app.services.market_data.providers import binance_provider

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def get(self, *_a, **_kw):
            raise RuntimeError("connection reset")

    monkeypatch.setattr(binance_provider.httpx, "Client", lambda **_kw: _Client())

    with pytest.raises(BarFetchError):
        binance_provider.BinanceProvider().get_bars("BTCUSDT", Timeframe.DAY_1, 250)


# --- and the page is told which it was ---------------------------------------------


def test_the_endpoint_reports_a_failure_rather_than_an_empty_chart(auth_client):
    """「no history」 and 「we could not ask」 need different words: one is
    permanent and one clears on its own."""
    from app.main import app
    from app.services.market_data.service import get_market_data_service

    clock = [0.0]
    service = _service(_Feed(BarFetchError("rate limited")), clock)
    app.dependency_overrides[get_market_data_service] = lambda: service
    try:
        resp = auth_client.get("/api/market/bars?symbol=AAPL")
    finally:
        app.dependency_overrides.pop(get_market_data_service, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["bars"] == []
    assert body["fetch_failed"] is True


def test_a_real_empty_history_is_not_reported_as_a_failure(auth_client):
    from app.main import app
    from app.services.market_data.service import get_market_data_service

    service = _service(_Feed([]), [0.0])
    app.dependency_overrides[get_market_data_service] = lambda: service
    try:
        body = auth_client.get("/api/market/bars?symbol=9999.TW").json()
    finally:
        app.dependency_overrides.pop(get_market_data_service, None)

    assert body["bars"] == []
    assert body["fetch_failed"] is False


def test_a_candle_with_no_volume_does_not_crash_the_endpoint(auth_client):
    """Bar.volume is `float | None` and the provider's NaN guard covers OHLC
    only, so a padded row really can arrive with no volume. The schema declared
    it required, which turns that row into a 500 on the chart."""
    from app.main import app
    from app.services.market_data.service import get_market_data_service

    bars = _bars("AAPL", Timeframe.DAY_1)
    bars[0] = Bar(
        symbol="AAPL",
        timeframe=Timeframe.DAY_1,
        timestamp=bars[0].timestamp,
        open=bars[0].open,
        high=bars[0].high,
        low=bars[0].low,
        close=bars[0].close,
        volume=None,
    )
    service = _service(_Feed(bars), [0.0])
    app.dependency_overrides[get_market_data_service] = lambda: service
    try:
        resp = auth_client.get("/api/market/bars?symbol=AAPL")
    finally:
        app.dependency_overrides.pop(get_market_data_service, None)

    assert resp.status_code == 200, resp.text
    assert resp.json()["bars"][0]["volume"] is None
