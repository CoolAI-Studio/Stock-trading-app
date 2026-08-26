"""Finer candles, and the four tables that must never disagree about them.

The owner: 「K線的單位不夠細，通常還要有小時 (譬如4hr / 12hr)，分鐘
(1/5/15/30分)，甚至可以到秒或tick.」

WHAT WAS ALREADY THERE. 1m, 5m, 15m and 1h existed end to end -- enum, TTL,
period, Binance mapping -- and the Strategies and Backtest pages already offered
them. Only the CHART hard-coded three buttons (日/週/月), so four working
timeframes were unreachable from the one screen that most needed them.

WHAT THE SOURCES ACTUALLY GIVE, measured by asking them:
  Yahoo's own error message lists its intervals:
    [1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 4h, 1d, 5d, 1wk, 1mo, 3mo]
    so 30m and 4h are NATIVE -- no resampling needed -- and 12h and 1s are
    refused outright. Rows for AAPL: 30m/60d=775, 4h/60d=119, 12h=rejected.
  Binance serves 1s, 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3d, 1w,
    1M -- so 12h works for crypto and not for stocks.

THAT ASYMMETRY IS THE DANGEROUS PART. If the chart simply lists 12h, a stock
user picks it, Yahoo returns an empty frame, and the app says 「暫時抓不到…可能
是被限流了」 -- a TRANSIENT sentence for a PERMANENT condition, sending somebody
to wait for something that will never happen. This app has one answer for that
shape of problem, used already for indicator panes and for unpriceable symbols:
declare what is supported, refuse the rest with a sentence, and never offer a
choice that cannot work.

FOUR SEPARATE TABLES are keyed by Timeframe, in four different files:
  base.py            _FIXED_DURATION   -- when a candle closes
  service.py         _DEFAULT_BAR_TTL_SEC -- how long to cache it
  yfinance_provider  _PERIOD_FOR       -- how much history to ask Yahoo for
  binance_provider   _BINANCE_INTERVAL -- Binance spells some differently
A member missing from any one of them is a KeyError at request time, on the
deployed box, for the one person using it. The completeness tests below are the
only thing that makes that impossible to ship.
"""

import pytest

from app.enums import DataSource
from app.services.market_data.base import (
    SUPPORTED_TIMEFRAMES,
    Timeframe,
    bar_end,
    supports_timeframe,
)

# --- the new candle sizes exist -------------------------------------------------


def test_the_sizes_the_owner_asked_for_are_all_there():
    values = {tf.value for tf in Timeframe}

    assert {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1wk", "1mo"} <= values


def test_thirty_minutes_and_four_hours_are_native_to_both_sources():
    """Not resampled from 1h. Yahoo serves both intervals itself, so the candle
    boundaries are the exchange's own rather than something this app invented
    -- which matters most for 4h on a 6.5-hour US session, where any boundary
    we chose ourselves would disagree with every other chart the owner looks
    at."""
    for timeframe in (Timeframe.MINUTE_30, Timeframe.HOUR_4):
        assert supports_timeframe(DataSource.YFINANCE, timeframe)
        assert supports_timeframe(DataSource.BINANCE, timeframe)


def test_twelve_hours_is_crypto_only_because_yahoo_refuses_it():
    """Measured: Yahoo answers 「interval=12h is not supported」 and returns
    nothing. Binance serves it natively."""
    assert supports_timeframe(DataSource.BINANCE, Timeframe.HOUR_12)
    assert not supports_timeframe(DataSource.YFINANCE, Timeframe.HOUR_12)


# --- every table answers for every candle size ------------------------------------


def test_every_timeframe_knows_when_its_candle_closes():
    """_FIXED_DURATION drives closed_bars(), which withholds the still-open
    candle. A missing entry is a KeyError on the request, and a wrong one feeds
    a strategy a candle that is still moving."""
    now = bar_end.__globals__["datetime"].now(bar_end.__globals__["UTC"])
    for timeframe in Timeframe:
        assert bar_end(now, timeframe) > now, f"{timeframe} has no duration"


def test_every_timeframe_has_a_cache_lifetime():
    from app.services.market_data.service import _DEFAULT_BAR_TTL_SEC

    missing = [tf.value for tf in Timeframe if tf not in _DEFAULT_BAR_TTL_SEC]

    assert not missing, f"no cache TTL for: {missing}"


def test_a_finer_candle_is_not_cached_longer_than_it_lives():
    """A 30-minute candle cached for an hour is a chart showing a bar that
    closed before the last one it drew."""
    from app.services.market_data.base import _FIXED_DURATION
    from app.services.market_data.service import _DEFAULT_BAR_TTL_SEC

    for timeframe, duration in _FIXED_DURATION.items():
        assert _DEFAULT_BAR_TTL_SEC[timeframe] <= duration.total_seconds(), (
            f"{timeframe.value} is cached for longer than the candle itself lasts"
        )


def test_every_timeframe_yahoo_supports_has_a_history_window():
    """區間現在是 limit 的函數而不是一張固定的表（原本那張表讓深一點的回測被
    靜默截斷），所以問的方式跟著改：每一個支援的週期都要算得出一個正數。"""
    from app.services.market_data.providers.yfinance_provider import _period_days

    missing = [
        tf.value for tf in SUPPORTED_TIMEFRAMES[DataSource.YFINANCE] if _period_days(tf, 300) <= 0
    ]

    assert not missing, f"no period for: {missing}"


def test_every_timeframe_binance_supports_has_its_own_spelling():
    from app.services.market_data.providers.binance_provider import _BINANCE_INTERVAL

    missing = [
        tf.value for tf in SUPPORTED_TIMEFRAMES[DataSource.BINANCE] if tf not in _BINANCE_INTERVAL
    ]

    assert not missing, f"no Binance interval for: {missing}"


def test_no_source_claims_a_timeframe_the_enum_does_not_have():
    for source, timeframes in SUPPORTED_TIMEFRAMES.items():
        for timeframe in timeframes:
            assert isinstance(timeframe, Timeframe), f"{source} claims {timeframe!r}"


def test_every_data_source_declares_something():
    """A source with no declared timeframes would silently offer an empty
    dropdown rather than failing."""
    for source in DataSource:
        assert SUPPORTED_TIMEFRAMES.get(source), f"{source} declares no timeframes"


# --- the window is deep enough to answer the question that was asked ---------------


def test_the_history_window_covers_the_deepest_chart_request():
    """Yahoo caps intraday history hard, and the app lets a chart ask for
    MAX_CHART_BARS candles. A window that cannot hold that many silently
    returns fewer -- a shorter chart reported as success, which this codebase
    treats as worse than an error.

    So where the cap makes the full depth impossible, the app must know it
    rather than discover it one short answer at a time.
    """
    from app.api.routers.market import MAX_CHART_BARS
    from app.services.market_data.base import max_bars_available
    from app.services.market_data.providers.yfinance_provider import _period_days

    for timeframe in SUPPORTED_TIMEFRAMES[DataSource.YFINANCE]:
        assert _period_days(timeframe, MAX_CHART_BARS) > 0
        assert max_bars_available(DataSource.YFINANCE, timeframe) > 0


def test_asking_deeper_than_the_source_can_go_is_said_out_loud():
    """MAX_CHART_BARS is 1000, and Yahoo will not serve 1000 candles at every
    interval. Measured at 30m: 775 for AAPL and 531 for 0050.TW, so the app has
    to know the ceiling rather than discover it one short answer at a time.

    The numbers are quoted for the SHORTEST session this app models, so they
    are a floor -- a US symbol yields more, and never fewer.
    """
    from app.services.market_data.base import max_bars_available

    assert max_bars_available(DataSource.YFINANCE, Timeframe.MINUTE_30) < 1000
    assert max_bars_available(DataSource.YFINANCE, Timeframe.DAY_1) >= 1000
    # 4h was the tightest of the lot at period=60d; measured again at 730d it
    # reaches ~1450, which is why the period was corrected rather than the
    # interval dropped.
    assert max_bars_available(DataSource.YFINANCE, Timeframe.HOUR_4) > 1000


# --- the app refuses a pair that cannot work --------------------------------------


def test_a_stock_on_a_crypto_only_candle_is_refused_with_a_sentence(auth_client):
    """NOT an empty chart, and NOT 「暫時抓不到…可能是被限流了」. That message
    tells somebody to wait for a condition that will never change."""
    resp = auth_client.get("/api/market/bars?symbol=AAPL&timeframe=12h")

    assert resp.status_code == 422
    detail = str(resp.json()["detail"])
    assert "12" in detail


def test_the_same_pair_is_refused_when_asking_for_indicators(auth_client):
    resp = auth_client.post(
        "/api/market/indicators",
        json={"symbol": "AAPL", "timeframe": "12h", "indicators": [{"name": "sma"}]},
    )

    assert resp.status_code == 422


def test_but_crypto_on_that_candle_is_allowed_through(auth_client):
    """The pair is legal, so it must reach the provider rather than being
    refused by a rule that only looked at the timeframe."""
    from app.main import app
    from app.services.market_data.service import MarketDataService, get_market_data_service

    class _Stub:
        data_source = DataSource.BINANCE
        asked: list = []

        def get_quotes(self, symbols):
            return {}

        def get_bars(self, symbol, timeframe, limit):
            _Stub.asked.append(timeframe)
            return []

    service = MarketDataService(providers={DataSource.BINANCE: _Stub()})
    app.dependency_overrides[get_market_data_service] = lambda: service
    try:
        resp = auth_client.get("/api/market/bars?symbol=BTCUSDT&timeframe=12h&data_source=binance")
    finally:
        app.dependency_overrides.pop(get_market_data_service, None)

    assert resp.status_code == 200, resp.text
    assert Timeframe.HOUR_12 in _Stub.asked


# --- and tells the page which ones to offer ----------------------------------------


def test_the_page_can_ask_which_candles_a_source_supports(auth_client):
    """The chart cannot lay out its buttons without this, and a list hard-coded
    in TypeScript would be a second answer that drifts the first time an
    interval is added."""
    body = auth_client.get("/api/market/timeframes").json()

    by_source = {entry["data_source"]: entry for entry in body["sources"]}
    yahoo = {tf["value"] for tf in by_source["yfinance"]["timeframes"]}
    binance = {tf["value"] for tf in by_source["binance"]["timeframes"]}

    assert "4h" in yahoo
    assert "12h" not in yahoo
    assert "12h" in binance


def test_each_one_comes_with_a_label_a_reader_recognises(auth_client):
    """「4h」 is the value the provider wants; 四小時線 is what the owner reads.
    Served from here so the three pages that show a timeframe cannot disagree."""
    body = auth_client.get("/api/market/timeframes").json()

    labels = {tf["value"]: tf["label"] for entry in body["sources"] for tf in entry["timeframes"]}
    assert labels["4h"] != "4h"
    assert all(label.strip() for label in labels.values())


def test_the_order_is_finest_to_coarsest(auth_client):
    """A dropdown that runs 日 週 月 1分 4小時 is a dropdown nobody can scan."""
    body = auth_client.get("/api/market/timeframes").json()

    for entry in body["sources"]:
        values = [tf["value"] for tf in entry["timeframes"]]
        assert values == sorted(values, key=_seconds_of), f"{entry['data_source']} is out of order"


def _seconds_of(value: str) -> float:
    from app.services.market_data.base import _FIXED_DURATION

    timeframe = Timeframe(value)
    if timeframe is Timeframe.MONTH_1:
        return 60 * 60 * 24 * 31
    return _FIXED_DURATION[timeframe].total_seconds()


def test_the_endpoint_needs_a_login(client):
    assert client.get("/api/market/timeframes").status_code == 401


# --- the last candle of a session closes when the session does ---------------------


def test_a_four_hour_candle_that_the_session_cut_short_is_released_on_time():
    """MEASURED against the real API: Yahoo aligns 4h candles to each
    exchange's own session open --
        AAPL     09:30 and 13:30 New York  (second candle = 2.5 hours)
        0050.TW  09:00 and 13:00 Taipei    (second candle = 30 MINUTES)
    -- while bar_end() adds a flat four hours, because it is deliberately
    calendar arithmetic.

    So the candle that finished for good at 13:30 Taipei is withheld by
    closed_bars() until 17:00. A 「4 小時線收盤跌破」 alert fires three and a
    half hours late, on a product whose first rule is 「警告不能停擺」. The US
    session loses ninety minutes to the same arithmetic, and 1h has always had
    a thirty-minute version of it.
    """
    from datetime import UTC, datetime

    from app.services.market_data.base import Bar, closed_bars

    # The 13:00 Taipei candle, which the session closed at 13:30.
    opened = datetime(2026, 8, 21, 5, 0, tzinfo=UTC)  # 13:00 Taipei
    bar = Bar(
        symbol="0050.TW",
        timeframe=Timeframe.HOUR_4,
        timestamp=opened,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1000.0,
    )
    just_after_the_close = datetime(2026, 8, 21, 5, 35, tzinfo=UTC)  # 13:35 Taipei

    kept = closed_bars([bar], now=just_after_the_close, data_source=DataSource.YFINANCE)

    assert kept == [bar], "the session had ended, so the candle was final"


def test_but_a_candle_still_inside_its_session_is_still_withheld():
    """The protection that must not be lost: a candle that is still moving
    must never reach a strategy."""
    from datetime import UTC, datetime

    from app.services.market_data.base import Bar, closed_bars

    opened = datetime(2026, 8, 21, 1, 0, tzinfo=UTC)  # 09:00 Taipei
    bar = Bar(
        symbol="0050.TW",
        timeframe=Timeframe.HOUR_4,
        timestamp=opened,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1000.0,
    )
    mid_session = datetime(2026, 8, 21, 3, 0, tzinfo=UTC)  # 11:00 Taipei

    assert closed_bars([bar], now=mid_session, data_source=DataSource.YFINANCE) == []


def test_a_market_that_never_closes_keeps_the_plain_arithmetic():
    """Crypto trades continuously and 4h divides 24 hours exactly, so there is
    no short candle to release early -- and clamping to a session that does not
    exist would release one that is still moving."""
    from datetime import UTC, datetime

    from app.services.market_data.base import Bar, closed_bars

    opened = datetime(2026, 8, 21, 8, 0, tzinfo=UTC)
    bar = Bar(
        symbol="BTCUSDT",
        timeframe=Timeframe.HOUR_4,
        timestamp=opened,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1000.0,
    )
    two_hours_in = datetime(2026, 8, 21, 10, 0, tzinfo=UTC)

    assert closed_bars([bar], now=two_hours_in, data_source=DataSource.BINANCE) == []


def test_a_daily_candle_is_not_released_at_the_session_close():
    """A daily candle is only final once the DAY is over. Clamping it to the
    session close would hand strategies a 「daily」 candle at 13:30, hours
    before the day it names has ended, and any after-hours adjustment Yahoo
    applies would then change a candle a strategy had already traded on."""
    from datetime import UTC, datetime

    from app.services.market_data.base import Bar, closed_bars

    bar = Bar(
        symbol="0050.TW",
        timeframe=Timeframe.DAY_1,
        timestamp=datetime(2026, 8, 21, 0, 0, tzinfo=UTC),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1000.0,
    )
    just_after_the_session = datetime(2026, 8, 21, 6, 0, tzinfo=UTC)

    assert closed_bars([bar], now=just_after_the_session, data_source=DataSource.YFINANCE) == []


def test_closed_bars_still_works_for_callers_that_know_no_source():
    """The signature gains an optional argument; every existing caller keeps
    the behaviour it had."""
    from datetime import UTC, datetime

    from app.services.market_data.base import Bar, closed_bars

    bar = Bar(
        symbol="0050.TW",
        timeframe=Timeframe.DAY_1,
        timestamp=datetime(2026, 8, 20, 0, 0, tzinfo=UTC),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1000.0,
    )

    assert closed_bars([bar], now=datetime(2026, 8, 22, 0, 0, tzinfo=UTC)) == [bar]


# --- a strategy cannot be saved onto a candle its own symbol has no data for -------


_TWELVE_HOUR_STRATEGY = """
class Strategy:
    def __init__(self):
        self.name = "twelve"
        self.symbol = "AAPL"
        self.timeframe = "12h"

    def on_bar(self, bar) -> str:
        return "BUY" if bar.close > bar.open else "HOLD"
"""


def test_a_strategy_on_a_candle_its_source_cannot_serve_is_refused_at_save(auth_client):
    """THE 「警告不能停擺」 CASE, and the one adding 12h could have created.

    _read_timeframe only checks the value against the enum -- it never sees the
    symbol -- so 「self.timeframe = '12h'」 on a US stock compiles happily. The
    market loop then fetches nothing for it on every tick, forever, and the
    strategy never fires. No error, no alert, no way to notice: a strategy that
    silently never runs is worse than one that fails loudly, because the owner
    believes they are being watched.
    """
    resp = auth_client.post(
        "/api/strategies",
        json={
            "name": "twelve",
            "symbol": "AAPL",
            "data_source": "yfinance",
            "source_code": _TWELVE_HOUR_STRATEGY,
        },
    )

    assert resp.status_code == 422
    assert "12" in str(resp.json()["detail"])


def test_the_very_same_strategy_is_fine_on_a_crypto_symbol(auth_client):
    """The rule is about the PAIR, not about the candle. Refusing 12h outright
    would take a real Binance interval away from the source that serves it."""
    resp = auth_client.post(
        "/api/strategies",
        json={
            "name": "twelve-crypto",
            "symbol": "BTCUSDT",
            "data_source": "binance",
            "source_code": _TWELVE_HOUR_STRATEGY.replace('"AAPL"', '"BTCUSDT"'),
        },
    )

    assert resp.status_code in (200, 201), resp.text


def test_moving_an_existing_strategy_to_a_source_without_that_candle_is_refused(auth_client):
    """The pair can also be broken by an EDIT -- same crypto strategy, symbol
    changed to a stock -- and the loop would go just as quiet."""
    created = auth_client.post(
        "/api/strategies",
        json={
            "name": "twelve-move",
            "symbol": "BTCUSDT",
            "data_source": "binance",
            "source_code": _TWELVE_HOUR_STRATEGY.replace('"AAPL"', '"BTCUSDT"'),
        },
    )
    assert created.status_code in (200, 201), created.text

    resp = auth_client.patch(
        f"/api/strategies/{created.json()['id']}",
        json={"symbol": "AAPL", "data_source": "yfinance"},
    )

    assert resp.status_code == 422


# --- seconds and ticks, said plainly ------------------------------------------------


def test_no_sub_minute_candle_is_offered_for_stocks():
    """MEASURED: Yahoo answers 「interval=1s is not supported」. There is no
    keyless source of sub-minute or tick data for equities, and a paid feed or
    a required API key is a blank on the deploy form for somebody who deploys
    this from a README button.

    Recorded as a test rather than a comment so that adding a 1s member without
    a source that serves it fails here instead of on the owner's screen.
    """
    for timeframe in SUPPORTED_TIMEFRAMES[DataSource.YFINANCE]:
        assert _seconds_of(timeframe.value) >= 60


@pytest.mark.parametrize("interval", ["1s", "tick", "30s"])
def test_an_interval_this_app_does_not_have_is_a_422_not_a_500(auth_client, interval):
    resp = auth_client.get(f"/api/market/bars?symbol=AAPL&timeframe={interval}")

    assert resp.status_code == 422
