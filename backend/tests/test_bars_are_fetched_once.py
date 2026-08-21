"""Two requests for the same candles must not become two provider calls.

INTRODUCED BY THE INDICATOR FEATURE, and worth its own file because of what it
touches. The chart now fires two requests at once: GET /api/market/bars for the
candles and POST /api/market/indicators for the lines over them. Both call
`MarketDataService.get_bars` with the same (source, symbol, timeframe), and
FastAPI runs sync endpoints in a threadpool, so they run concurrently and
really do overlap.

The cache made that free -- once it is warm. On a COLD cache both callers miss,
both go to yfinance, and one chart view costs two upstream requests. That is
the exact currency the AAPL bug was paid in: Render's free tier spins down when
idle, so every wake starts cold, and the market loop has already spent that
IP's budget on its quote burst before the chart asks. Doubling the chart's cost
on precisely the request that is most likely to be rate limited is how 「查不到
AAPL 的歷史資料」 comes back.

The fix is the standard one and the test is the only thing that keeps it: the
second caller waits for the first and then reads the cache, rather than
starting a second fetch. Nothing here asserts on timing -- it counts calls.
"""

import threading
import time

from app.models.enums import DataSource
from app.services.market_data.base import Bar, Timeframe
from app.services.market_data.service import MarketDataService


def _bars(count: int = 5) -> list[Bar]:
    from datetime import UTC, datetime, timedelta

    start = datetime(2026, 1, 5, tzinfo=UTC)
    return [
        Bar(
            symbol="AAPL",
            timeframe=Timeframe.DAY_1,
            timestamp=start + timedelta(days=i),
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
            volume=1000.0,
        )
        for i in range(count)
    ]


class _SlowFeed:
    """A provider that takes long enough for a second caller to arrive."""

    data_source = DataSource.YFINANCE

    def __init__(self, delay: float = 0.15) -> None:
        self.delay = delay
        self.calls = 0
        self.lock = threading.Lock()

    def get_quotes(self, symbols):
        return {}

    def get_bars(self, symbol, timeframe, limit):
        with self.lock:
            self.calls += 1
        time.sleep(self.delay)
        return _bars()


def _race(service: MarketDataService, symbol: str, count: int = 4) -> list[list[Bar]]:
    results: list[list[Bar]] = [[] for _ in range(count)]
    start = threading.Barrier(count)

    def run(index: int) -> None:
        start.wait()
        results[index] = service.get_bars(symbol, Timeframe.DAY_1, DataSource.YFINANCE, limit=250)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    # join(timeout) does not raise. A deadlock in the per-key lock would leave
    # exactly one caller through -- which is what `calls == 1` asserts -- so
    # without this the test is GREEN for a hung service, and pytest hangs at
    # exit instead of failing.
    assert not any(thread.is_alive() for thread in threads), "a caller never came back"
    return results


def test_four_callers_at_once_cost_one_upstream_request():
    """The chart's own two requests, plus a page refresh landing on top."""
    feed = _SlowFeed()
    service = MarketDataService(providers={DataSource.YFINANCE: feed})

    _race(service, "AAPL")

    assert feed.calls == 1


def test_every_one_of_them_still_gets_the_candles():
    """Serialising must not mean the losers get an empty list -- that would
    trade a rate limit for the blank chart it was supposed to prevent."""
    feed = _SlowFeed()
    service = MarketDataService(providers={DataSource.YFINANCE: feed})

    results = _race(service, "AAPL")

    assert all(len(result) == 5 for result in results)


def test_different_symbols_do_not_queue_behind_each_other():
    """A single global lock would serialise the market loop's whole sweep
    behind one slow symbol. Alerts not going out is this product's worst
    failure; the lock has to be per symbol."""
    feed = _SlowFeed(delay=0.3)
    service = MarketDataService(providers={DataSource.YFINANCE: feed})

    started = time.monotonic()
    threads = [
        threading.Thread(
            target=service.get_bars,
            args=(symbol, Timeframe.DAY_1, DataSource.YFINANCE),
            kwargs={"limit": 250},
        )
        for symbol in ("AAPL", "MSFT", "2330.TW", "0050.TW")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    elapsed = time.monotonic() - started

    assert feed.calls == 4
    # Four 0.3s fetches serialised would be 1.2s. Generous room for a loaded
    # CI box, and still nowhere near serial.
    assert elapsed < 0.9, f"symbols queued behind each other: {elapsed:.2f}s"


# --- but it must not make the market loop wait for somebody else's window ---------


def test_a_shallower_chart_request_does_not_hold_up_a_deeper_loop_request():
    """The chart asks for one depth and the market loop asks for another.

    The lock exists to collapse IDENTICAL requests into one fetch. Two
    different depths are not identical: a 250-bar answer cannot satisfy a
    300-bar question -- the provider tails to exactly what was asked -- so the
    deeper caller waits for a fetch it then has to repeat. It pays the cost of
    the lock and gets none of the benefit, and the caller it pays it to is the
    market loop. 「警告不能停擺」 outranks the chart.
    """
    feed = _SlowFeed(delay=0.3)
    service = MarketDataService(providers={DataSource.YFINANCE: feed})

    started = time.monotonic()
    threads = [
        threading.Thread(
            target=service.get_bars,
            args=("AAPL", Timeframe.DAY_1, DataSource.YFINANCE),
            kwargs={"limit": depth},
        )
        for depth in (250, 300)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    elapsed = time.monotonic() - started

    assert elapsed < 0.55, f"the deeper request queued behind the shallower one: {elapsed:.2f}s"


def test_the_chart_asks_for_the_same_depth_the_market_loop_does():
    """So that the two actually share a cache entry and one fetch serves both.

    Different defaults mean the chart's answer can never satisfy the loop and
    the loop's can never satisfy the chart, so every chart view costs an extra
    rate-limited request on an IP that is already the reason a stock once read
    as having no history.
    """
    from app.api.routers import market as market_router
    from app.schemas.market import IndicatorRequest
    from app.services.market_data.service import DEFAULT_BAR_LIMIT

    bars_default = market_router.get_bars.__defaults__
    assert DEFAULT_BAR_LIMIT in [
        getattr(value, "default", value) for value in (bars_default or ())
    ], "GET /bars no longer defaults to the depth the market loop uses"
    assert IndicatorRequest.model_fields["limit"].default == DEFAULT_BAR_LIMIT


def test_a_failure_slower_than_the_cooldown_is_still_reported_as_a_failure():
    """The stamp is taken when the fetch STARTS, so a fetch that takes longer
    than the cooldown is already 「expired」 by the time anyone asks -- the page
    is told 「there is no history」 about the one request that actually failed,
    which is the exact confusion this flag was added to end.
    """
    from app.services.market_data.base import BarFetchError
    from app.services.market_data.service import FAILED_FETCH_RETRY_SEC

    clock = [0.0]

    class _SlowFailure:
        data_source = DataSource.YFINANCE

        def get_quotes(self, symbols):
            return {}

        def get_bars(self, symbol, timeframe, limit):
            # The fetch itself burns more than the whole cooldown window.
            clock[0] += FAILED_FETCH_RETRY_SEC + 10.0
            raise BarFetchError("timed out")

    service = MarketDataService(
        providers={DataSource.YFINANCE: _SlowFailure()}, clock=lambda: clock[0]
    )
    service.get_bars("AAPL", Timeframe.DAY_1, DataSource.YFINANCE, limit=250)

    assert service.bar_fetch_failed("AAPL", Timeframe.DAY_1, DataSource.YFINANCE)
