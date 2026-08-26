"""Candle (OHLC) plumbing: the Bar type, closed-vs-forming, provider mapping
and the history cache that keeps yfinance from blocking this IP."""

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.enums import DataSource
from app.services.market_data.base import Bar, Timeframe, bar_end, closed_bars
from app.services.market_data.providers import yfinance_provider
from app.services.market_data.providers.mock_provider import MockProvider
from app.services.market_data.service import MarketDataService


def _bar(timestamp: datetime, close: float = 100.0, timeframe=Timeframe.WEEK_1) -> Bar:
    return Bar(
        symbol="2330.TW",
        timeframe=timeframe,
        timestamp=timestamp,
        open=close - 1,
        high=close + 2,
        low=close - 2,
        close=close,
        volume=1000.0,
    )


# --- when does a candle end -------------------------------------------------


@pytest.mark.parametrize(
    ("timeframe", "expected"),
    [
        (Timeframe.MINUTE_1, datetime(2026, 8, 3, 9, 31, tzinfo=UTC)),
        (Timeframe.MINUTE_5, datetime(2026, 8, 3, 9, 35, tzinfo=UTC)),
        (Timeframe.MINUTE_15, datetime(2026, 8, 3, 9, 45, tzinfo=UTC)),
        (Timeframe.HOUR_1, datetime(2026, 8, 3, 10, 30, tzinfo=UTC)),
    ],
)
def test_bar_end_for_intraday_timeframes(timeframe, expected):
    assert bar_end(datetime(2026, 8, 3, 9, 30, tzinfo=UTC), timeframe) == expected


def test_a_weekly_candle_ends_a_week_after_it_opened():
    monday = datetime(2026, 8, 3, tzinfo=UTC)
    assert bar_end(monday, Timeframe.WEEK_1) == datetime(2026, 8, 10, tzinfo=UTC)


def test_a_monthly_candle_ends_on_the_first_of_the_next_month():
    end = bar_end(datetime(2026, 8, 1, tzinfo=UTC), Timeframe.MONTH_1)
    assert end == datetime(2026, 9, 1, tzinfo=UTC)


def test_a_december_candle_rolls_into_the_next_year():
    end = bar_end(datetime(2026, 12, 1, tzinfo=UTC), Timeframe.MONTH_1)
    assert end == datetime(2027, 1, 1, tzinfo=UTC)


# yfinance labels every candle at midnight in the EXCHANGE's timezone, so for
# any market east of UTC the UTC instant lands in the previous calendar month.
# Truncating that instant to day 1 therefore names the wrong month, and the
# candle still being built gets reported as finished -- for the whole month.
def test_a_monthly_candle_east_of_utc_ends_when_its_own_month_does():
    """2330.TW's August candle is stamped 2026-08-01 00:00+08:00, which is
    2026-07-31 16:00 UTC. It ends at 2026-09-01 00:00+08:00, not on 1 August."""
    taipei_august = datetime(2026, 7, 31, 16, 0, tzinfo=UTC)

    assert bar_end(taipei_august, Timeframe.MONTH_1) == datetime(2026, 8, 31, 16, 0, tzinfo=UTC)


def test_a_monthly_candle_east_of_utc_survives_a_short_february():
    """The March candle is stamped 2026-02-28 16:00 UTC. Advancing the label
    by one month lands on 28 March -- three days early -- so the month has to
    be resolved in the exchange's own calendar, not by date arithmetic on UTC."""
    taipei_march = datetime(2026, 2, 28, 16, 0, tzinfo=UTC)

    assert bar_end(taipei_march, Timeframe.MONTH_1) == datetime(2026, 3, 31, 16, 0, tzinfo=UTC)


def test_a_monthly_candle_west_of_utc_ends_when_its_own_month_does():
    """The mirror image: New York stamps August at 2026-08-01 04:00 UTC."""
    new_york_august = datetime(2026, 8, 1, 4, 0, tzinfo=UTC)

    assert bar_end(new_york_august, Timeframe.MONTH_1) == datetime(2026, 9, 1, 4, 0, tzinfo=UTC)


def test_the_forming_monthly_candle_of_a_taiwan_symbol_is_dropped():
    """The regression that matters: mid-August, a monthly strategy on a Taiwan
    symbol must not be handed August as though it had closed."""
    july = _bar(datetime(2026, 6, 30, 16, 0, tzinfo=UTC), timeframe=Timeframe.MONTH_1)
    august = _bar(datetime(2026, 7, 31, 16, 0, tzinfo=UTC), timeframe=Timeframe.MONTH_1)

    settled = closed_bars([july, august], now=datetime(2026, 8, 18, 6, 0, tzinfo=UTC))

    assert settled == [july]


# --- closed vs still forming ------------------------------------------------


def test_the_candle_still_forming_is_dropped():
    """'On the close of the second candle' is meaningless if a half-built
    candle counts as one, so the newest row only survives once its own end
    time has passed."""
    bars = [
        _bar(datetime(2026, 8, 3, tzinfo=UTC)),
        _bar(datetime(2026, 8, 10, tzinfo=UTC)),  # this week -- still forming
    ]
    now = datetime(2026, 8, 12, tzinfo=UTC)

    kept = closed_bars(bars, now=now)

    assert [b.timestamp for b in kept] == [datetime(2026, 8, 3, tzinfo=UTC)]


def test_a_candle_that_has_just_closed_is_kept():
    bars = [_bar(datetime(2026, 8, 3, tzinfo=UTC))]
    now = datetime(2026, 8, 10, tzinfo=UTC)

    assert closed_bars(bars, now=now) == bars


def test_closed_bars_on_an_empty_series():
    assert closed_bars([], now=datetime(2026, 8, 12, tzinfo=UTC)) == []


# --- yfinance mapping (no network) ------------------------------------------


class _FakeTicker:
    """Stands in for yf.Ticker. Records the call so a test can assert the
    interval actually asked for."""

    calls: list[dict] = []

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def history(self, **kwargs):
        _FakeTicker.calls.append({"symbol": self.symbol, **kwargs})
        index = pd.DatetimeIndex(
            [datetime(2026, 7, 27, tzinfo=UTC), datetime(2026, 8, 3, tzinfo=UTC)]
        )
        return pd.DataFrame(
            {
                "Open": [100.0, 104.0],
                "High": [106.0, 109.0],
                "Low": [99.0, 103.0],
                "Close": [104.0, 108.0],
                "Volume": [1200.0, 1500.0],
            },
            index=index,
        )


def test_yfinance_history_maps_to_bars(monkeypatch):
    _FakeTicker.calls = []
    monkeypatch.setattr(yfinance_provider.yf, "Ticker", _FakeTicker)

    bars = yfinance_provider.YFinanceProvider().get_bars("2330.TW", Timeframe.WEEK_1, limit=10)

    assert [b.close for b in bars] == [104.0, 108.0]
    assert bars[0].open == 100.0
    assert bars[0].high == 106.0
    assert bars[0].low == 99.0
    assert bars[0].volume == 1200.0
    assert bars[-1].timestamp == datetime(2026, 8, 3, tzinfo=UTC)
    assert bars[-1].timeframe is Timeframe.WEEK_1
    assert bars[-1].symbol == "2330.TW"
    # The interval string the strategy declared reaches yfinance verbatim.
    assert _FakeTicker.calls[0]["interval"] == "1wk"


def test_yfinance_history_limit_takes_the_newest_bars(monkeypatch):
    _FakeTicker.calls = []
    monkeypatch.setattr(yfinance_provider.yf, "Ticker", _FakeTicker)

    bars = yfinance_provider.YFinanceProvider().get_bars("2330.TW", Timeframe.WEEK_1, limit=1)

    assert [b.close for b in bars] == [108.0]


def test_a_naive_timestamp_is_read_as_utc(monkeypatch):
    class _NaiveTicker(_FakeTicker):
        def history(self, **kwargs):
            return pd.DataFrame(
                {"Open": [1.0], "High": [2.0], "Low": [0.5], "Close": [1.5], "Volume": [10.0]},
                index=pd.DatetimeIndex([datetime(2026, 8, 3)]),
            )

    monkeypatch.setattr(yfinance_provider.yf, "Ticker", _NaiveTicker)

    bars = yfinance_provider.YFinanceProvider().get_bars("X", Timeframe.DAY_1, limit=10)

    assert bars[0].timestamp == datetime(2026, 8, 3, tzinfo=UTC)


def test_a_row_with_no_close_is_skipped(monkeypatch):
    class _GappyTicker(_FakeTicker):
        def history(self, **kwargs):
            index = pd.DatetimeIndex(
                [datetime(2026, 8, 3, tzinfo=UTC), datetime(2026, 8, 10, tzinfo=UTC)]
            )
            nan = float("nan")
            return pd.DataFrame(
                {
                    "Open": [1.0, nan],
                    "High": [2.0, nan],
                    "Low": [0.5, nan],
                    "Close": [1.5, nan],
                    "Volume": [10.0, nan],
                },
                index=index,
            )

    monkeypatch.setattr(yfinance_provider.yf, "Ticker", _GappyTicker)

    bars = yfinance_provider.YFinanceProvider().get_bars("X", Timeframe.WEEK_1, limit=10)

    assert [b.close for b in bars] == [1.5]


def test_a_fetch_that_raises_is_reported_as_a_failure_not_as_no_bars(monkeypatch):
    """This used to assert []. It was wrong, and it cost fifteen minutes of a
    perfectly good stock reading as delisted.

    The service CACHES what get_bars returns. With a failure and an absence
    arriving as the same empty list, one rate-limited response on a shared
    deployment IP was stored as 「AAPL has no history」 for the whole TTL. The
    caller has to be able to tell the two apart to decide how long to wait, so
    a failure raises now -- see BarFetchError and
    tests/test_bars_failure_is_not_an_answer.py.
    """
    import pytest

    from app.services.market_data.base import BarFetchError

    class _RaisingTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        def history(self, **kwargs):
            raise RuntimeError("no data found for this symbol")

    monkeypatch.setattr(yfinance_provider.yf, "Ticker", _RaisingTicker)

    with pytest.raises(BarFetchError):
        yfinance_provider.YFinanceProvider().get_bars("NOPE", Timeframe.WEEK_1, limit=10)


def test_mock_provider_only_produces_closed_bars():
    bars = MockProvider(base_prices={"AAPL": 100.0}).get_bars("AAPL", Timeframe.WEEK_1, limit=8)

    assert len(bars) == 8
    assert closed_bars(bars) == bars
    assert all(b.low <= b.close <= b.high for b in bars)
    assert all(b.low <= b.open <= b.high for b in bars)


# --- the history cache ------------------------------------------------------


class _StubBarProvider:
    """Serves a per-symbol candle series and counts fetches. Symbols it does
    not know return nothing at all -- which is what yfinance does for a typo,
    a delisting, or a Taiwan ticker missing its .TW suffix."""

    data_source = DataSource.YFINANCE

    def __init__(self, series: dict[str, list[Bar]]) -> None:
        self.series = series
        self.calls: list[tuple[str, Timeframe]] = []

    def get_quotes(self, symbols):
        return {}

    def get_bars(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Bar]:
        self.calls.append((symbol, timeframe))
        return list(self.series.get(symbol, []))[-limit:]


def _weekly_series(count: int, start: datetime, base: float = 100.0) -> list[Bar]:
    return [_bar(start + timedelta(weeks=i), close=base + i) for i in range(count)]


def test_history_is_not_refetched_on_every_poll():
    """A closed weekly candle cannot change. Re-downloading ten years of them
    every MARKET_DATA_POLL_INTERVAL_SEC is what gets an IP blocked."""
    provider = _StubBarProvider({"2330.TW": _weekly_series(5, datetime(2026, 1, 5, tzinfo=UTC))})
    fake_time = {"t": 0.0}
    service = MarketDataService(
        providers={DataSource.YFINANCE: provider},
        bar_ttl_sec={Timeframe.WEEK_1: 3600.0},
        clock=lambda: fake_time["t"],
    )

    for _ in range(20):
        fake_time["t"] += 5.0
        service.get_bars("2330.TW", Timeframe.WEEK_1, DataSource.YFINANCE)

    assert len(provider.calls) == 1


def test_history_is_refetched_once_its_ttl_expires():
    provider = _StubBarProvider({"2330.TW": _weekly_series(5, datetime(2026, 1, 5, tzinfo=UTC))})
    fake_time = {"t": 0.0}
    service = MarketDataService(
        providers={DataSource.YFINANCE: provider},
        bar_ttl_sec={Timeframe.WEEK_1: 3600.0},
        clock=lambda: fake_time["t"],
    )

    service.get_bars("2330.TW", Timeframe.WEEK_1, DataSource.YFINANCE)
    fake_time["t"] += 3601.0
    service.get_bars("2330.TW", Timeframe.WEEK_1, DataSource.YFINANCE)

    assert len(provider.calls) == 2


def test_each_timeframe_is_cached_separately():
    provider = _StubBarProvider({"2330.TW": _weekly_series(5, datetime(2026, 1, 5, tzinfo=UTC))})
    service = MarketDataService(providers={DataSource.YFINANCE: provider})

    service.get_bars("2330.TW", Timeframe.WEEK_1, DataSource.YFINANCE)
    service.get_bars("2330.TW", Timeframe.DAY_1, DataSource.YFINANCE)

    assert [tf for _, tf in provider.calls] == [Timeframe.WEEK_1, Timeframe.DAY_1]


def test_an_unresolvable_symbol_is_not_retried_on_every_poll():
    """The quote cache learned this the expensive way: a symbol the provider
    never returns must not turn into one fetch every five seconds."""
    provider = _StubBarProvider({})
    fake_time = {"t": 0.0}
    service = MarketDataService(
        providers={DataSource.YFINANCE: provider},
        bar_ttl_sec={Timeframe.WEEK_1: 3600.0},
        clock=lambda: fake_time["t"],
    )

    for _ in range(10):
        fake_time["t"] += 5.0
        assert service.get_bars("DELISTED.OLD", Timeframe.WEEK_1, DataSource.YFINANCE) == []

    assert len(provider.calls) == 1


def test_an_unresolvable_symbol_does_not_freeze_another_symbols_history():
    """Sibling of the quote-cache regression: one bad symbol must never stop
    a good one from seeing new candles."""
    start = datetime(2026, 1, 5, tzinfo=UTC)
    series = {"2330.TW": _weekly_series(5, start)}
    provider = _StubBarProvider(series)
    fake_time = {"t": 0.0}
    service = MarketDataService(
        providers={DataSource.YFINANCE: provider},
        bar_ttl_sec={Timeframe.WEEK_1: 60.0},
        clock=lambda: fake_time["t"],
    )

    service.get_bars("2330.TW", Timeframe.WEEK_1, DataSource.YFINANCE)
    service.get_bars("DELISTED.OLD", Timeframe.WEEK_1, DataSource.YFINANCE)

    # A week goes by: the good symbol gains a candle.
    series["2330.TW"] = _weekly_series(6, start)
    fake_time["t"] += 61.0

    service.get_bars("DELISTED.OLD", Timeframe.WEEK_1, DataSource.YFINANCE)
    fresh = service.get_bars("2330.TW", Timeframe.WEEK_1, DataSource.YFINANCE)

    assert len(fresh) == 6


def test_a_fetch_that_comes_back_empty_keeps_the_last_good_history():
    """A transient yfinance failure must not look like 'this strategy has no
    history yet' and silently restart its warm-up."""
    start = datetime(2026, 1, 5, tzinfo=UTC)
    series = {"2330.TW": _weekly_series(5, start)}
    provider = _StubBarProvider(series)
    fake_time = {"t": 0.0}
    service = MarketDataService(
        providers={DataSource.YFINANCE: provider},
        bar_ttl_sec={Timeframe.WEEK_1: 60.0},
        clock=lambda: fake_time["t"],
    )

    service.get_bars("2330.TW", Timeframe.WEEK_1, DataSource.YFINANCE)
    series["2330.TW"] = []
    fake_time["t"] += 61.0

    assert len(service.get_bars("2330.TW", Timeframe.WEEK_1, DataSource.YFINANCE)) == 5


def test_the_forming_candle_never_reaches_a_strategy():
    now_ts = datetime.now(UTC)
    # A weekly candle that opened yesterday is still being built.
    forming = _bar(now_ts - timedelta(days=1), close=999.0)
    settled = _bar(now_ts - timedelta(days=9), close=111.0)
    provider = _StubBarProvider({"2330.TW": [settled, forming]})
    service = MarketDataService(providers={DataSource.YFINANCE: provider})

    bars = service.get_bars("2330.TW", Timeframe.WEEK_1, DataSource.YFINANCE)

    assert [b.close for b in bars] == [111.0]
