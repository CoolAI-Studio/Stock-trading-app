from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from app.models.enums import DataSource


class BarFetchError(Exception):
    """The provider could not be asked -- rate limited, unreachable, refused.

    Distinct from an empty list, which means 「asked, and this symbol has no
    candles in that window」. They used to be the same value, and the service
    cached that value as an answer: one 429 on a shared deployment IP made a
    stock with fifty years of history read as having none, for the full
    fifteen-minute TTL of a daily chart.

    Raised rather than returned as None, because a None is easy to forget to
    check and this one must never be silently treated as 「no history」 again.
    """


@dataclass
class Quote:
    symbol: str
    data_source: DataSource
    price: Decimal
    prev_close: Decimal | None = None
    change_pct: Decimal | None = None
    volume: Decimal | None = None
    quote_time: datetime | None = None
    # What the price is denominated in. None on rows written before this
    # existed -- defaulting to a currency would relabel every one of them, and
    # a wrong label is worse than a missing one.
    currency: str | None = None
    # When the PROVIDER answered, stamped by MarketDataService rather than by
    # a provider. Distinct from quote_time (when the exchange says the trade
    # happened) and from 「now」: a quote served from cache after a failed
    # refresh keeps the time it actually arrived, which is the only way the
    # rest of the app can tell a live price from a held one. None when the
    # quote was built by hand and never went through a fetch.
    fetched_at: datetime | None = None


class QuoteProvider(Protocol):
    data_source: DataSource

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]: ...


class Timeframe(StrEnum):
    """The candle sizes a retail user actually asks for.

    The values are yfinance's own interval strings rather than names of our
    own: the provider the owner uses gets them verbatim, and a strategy that
    writes `self.timeframe = "1wk"` is saying the same thing the data source
    says. Weekly is the one the owner asked for by name.
    """

    MINUTE_1 = "1m"
    MINUTE_5 = "5m"
    MINUTE_15 = "15m"
    MINUTE_30 = "30m"
    HOUR_1 = "1h"
    # Native on BOTH sources, not resampled from 1h. Yahoo lists 4h in its own
    # supported set, so the boundaries are the exchange's own -- which matters
    # most here, because a 6.5-hour US session does not divide into four-hour
    # candles, and any boundary this app chose for itself would disagree with
    # every other chart the owner looks at.
    HOUR_4 = "4h"
    # CRYPTO ONLY. Yahoo answers 「interval=12h is not supported」 and returns
    # nothing at all; Binance serves it natively. See SUPPORTED_TIMEFRAMES --
    # offering this on a stock would produce an empty frame, which this app
    # reports as 「暫時抓不到…可能是被限流了」: a transient sentence for a
    # permanent condition, telling somebody to wait for something that will
    # never happen.
    HOUR_12 = "12h"
    DAY_1 = "1d"
    WEEK_1 = "1wk"
    MONTH_1 = "1mo"


# Which candle sizes each source actually serves.
#
# DECLARED, NOT DERIVED, and the reason is the same one that governs indicator
# panes: a choice that cannot work must never be offered. Measured by asking the
# providers themselves --
#
#   Yahoo returns its own list in an error message:
#     [1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 4h, 1d, 5d, 1wk, 1mo, 3mo]
#   Binance serves 1s, 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3d,
#     1w, 1M.
#
# So 12h is crypto-only. Asking Yahoo for it returns an EMPTY FRAME rather than
# an error, which this app's provider layer -- correctly, for every other case
# -- reports as a failed fetch. The page then says 「暫時抓不到…可能是被限流
# 了」, which would send somebody back to wait for a condition that is
# permanent. Refusing the pair up front is the only honest answer.
SUPPORTED_TIMEFRAMES: dict[DataSource, tuple[Timeframe, ...]] = {
    DataSource.YFINANCE: (
        Timeframe.MINUTE_1,
        Timeframe.MINUTE_5,
        Timeframe.MINUTE_15,
        Timeframe.MINUTE_30,
        Timeframe.HOUR_1,
        Timeframe.HOUR_4,
        Timeframe.DAY_1,
        Timeframe.WEEK_1,
        Timeframe.MONTH_1,
    ),
    DataSource.BINANCE: (
        Timeframe.MINUTE_1,
        Timeframe.MINUTE_5,
        Timeframe.MINUTE_15,
        Timeframe.MINUTE_30,
        Timeframe.HOUR_1,
        Timeframe.HOUR_4,
        Timeframe.HOUR_12,
        Timeframe.DAY_1,
        Timeframe.WEEK_1,
        Timeframe.MONTH_1,
    ),
}

# What the owner reads. 「4h」 is the string the provider wants; 四小時線 is the
# thing a person recognises. Served from the API so that the chart, the
# strategy form and the backtest form cannot drift into three different names
# for one candle.
TIMEFRAME_LABELS: dict[Timeframe, str] = {
    Timeframe.MINUTE_1: "1 分線",
    Timeframe.MINUTE_5: "5 分線",
    Timeframe.MINUTE_15: "15 分線",
    Timeframe.MINUTE_30: "30 分線",
    Timeframe.HOUR_1: "1 小時線",
    Timeframe.HOUR_4: "4 小時線",
    Timeframe.HOUR_12: "12 小時線",
    Timeframe.DAY_1: "日線",
    Timeframe.WEEK_1: "週線",
    Timeframe.MONTH_1: "月線",
}

# How far back each source will actually go, per interval, in CANDLES.
#
# Yahoo caps intraday history hard and, past the cap, hands back an empty frame
# rather than a shorter one. The app lets a chart ask for up to MAX_CHART_BARS,
# so without this it would ask for 1000 four-hour candles, receive 119, and
# report success -- a silently shortened chart, which this codebase treats as
# worse than an error. Measured against the real API, generous by a little
# rather than optimistic.
_MAX_BARS: dict[DataSource, dict[Timeframe, int]] = {
    # A LOWER BOUND, quoted for the SHORTEST session this app models (Taiwan,
    # 4.5 hours). A US symbol yields more. Measured 30m/60d: AAPL 775 candles,
    # 0050.TW 531 -- 46% fewer for the same request, so a table written from
    # the US session would overstate Taiwan, and this number exists precisely
    # to say 「this depth is not available」 before the short answer arrives.
    DataSource.YFINANCE: {
        Timeframe.MINUTE_1: 1_300,
        Timeframe.MINUTE_5: 3_100,
        Timeframe.MINUTE_15: 1_000,
        Timeframe.MINUTE_30: 520,
        Timeframe.HOUR_1: 3_500,
        Timeframe.HOUR_4: 1_400,
        Timeframe.DAY_1: 1_250,  # 5y of trading days, not calendar days
        Timeframe.WEEK_1: 500,  # 10y
        # The real ceiling is 「how long has this been listed」, which no table
        # knows. Measured 168 for AAPL at period=max.
        Timeframe.MONTH_1: 1_200,
    },
    DataSource.BINANCE: {
        # Binance serves a flat 1000 per request and this app makes one
        # request, so every interval has the same ceiling.
        timeframe: 1_000
        for timeframe in (
            Timeframe.MINUTE_1,
            Timeframe.MINUTE_5,
            Timeframe.MINUTE_15,
            Timeframe.MINUTE_30,
            Timeframe.HOUR_1,
            Timeframe.HOUR_4,
            Timeframe.HOUR_12,
            Timeframe.DAY_1,
            Timeframe.WEEK_1,
            Timeframe.MONTH_1,
        )
    },
}


def supports_timeframe(data_source: DataSource, timeframe: Timeframe) -> bool:
    """Whether this source serves this candle size at all."""
    return timeframe in SUPPORTED_TIMEFRAMES.get(data_source, ())


def max_bars_available(data_source: DataSource, timeframe: Timeframe) -> int:
    """How many candles of this size the source will actually part with."""
    return _MAX_BARS.get(data_source, {}).get(timeframe, 0)


# Daily is the least surprising thing to hand a strategy that never said
# which candle it wanted -- an intraday default would quietly burn provider
# quota, and a weekly one would leave it silent for a week.
DEFAULT_TIMEFRAME = Timeframe.DAY_1


@dataclass(frozen=True)
class Bar:
    """One OHLC candle.

    Prices are floats, not the Decimal the rest of the money path uses,
    because this object is handed straight to user-authored strategy code:
    `bar.close * 1.02` against a Decimal raises TypeError, and an AI writing
    indicator arithmetic will do exactly that. The one place a bar's price
    becomes money -- an order's signal_price -- converts explicitly.

    `timestamp` is the candle's OPEN time in UTC, which is how both yfinance
    and Binance label their rows.
    """

    symbol: str
    timeframe: Timeframe
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None


class BarProvider(Protocol):
    data_source: DataSource

    def get_bars(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Bar]: ...


_FIXED_DURATION: dict[Timeframe, timedelta] = {
    Timeframe.MINUTE_1: timedelta(minutes=1),
    Timeframe.MINUTE_5: timedelta(minutes=5),
    Timeframe.MINUTE_15: timedelta(minutes=15),
    Timeframe.MINUTE_30: timedelta(minutes=30),
    Timeframe.HOUR_1: timedelta(hours=1),
    Timeframe.HOUR_4: timedelta(hours=4),
    Timeframe.HOUR_12: timedelta(hours=12),
    Timeframe.DAY_1: timedelta(days=1),
    Timeframe.WEEK_1: timedelta(weeks=1),
}


def bar_end(timestamp: datetime, timeframe: Timeframe) -> datetime:
    """When the candle opened at `timestamp` stops accepting trades.

    Deliberately calendar arithmetic rather than exchange hours: a daily bar
    is treated as open until the next calendar day even though the session
    ends hours earlier. Erring late only ever delays a signal by a few hours;
    erring early would feed a strategy a candle that is still moving, which
    is the failure this whole entry point exists to prevent.
    """
    if timeframe is Timeframe.MONTH_1:
        start = _month_start_nearest(timestamp)
        return _next_month(start) + (timestamp - start)
    return timestamp + _FIXED_DURATION[timeframe]


def _next_month(first_of_month: datetime) -> datetime:
    return (
        first_of_month.replace(year=first_of_month.year + 1, month=1)
        if first_of_month.month == 12
        else first_of_month.replace(month=first_of_month.month + 1)
    )


def _month_start_nearest(timestamp: datetime) -> datetime:
    """Midnight on the 1st of the month this candle belongs to, as a UTC instant.

    A monthly candle is labelled midnight on the 1st in the EXCHANGE's own
    timezone, and the provider hands that over converted to UTC. For anything
    east of UTC the result lands in the previous calendar month -- 2330.TW's
    August candle is 2026-07-31 16:00 UTC -- so reading the month off the UTC
    date names July, and the candle still being built gets called finished for
    the whole of August.

    The offset is recovered instead of assumed: whichever month boundary the
    label is nearest to is the one it means, since every real UTC offset is
    under 14 hours and no month is shorter than 28 days. Keeping the leftover
    as an offset is also what survives a short February -- advancing the label
    itself by a month would turn 28 February into 28 March, three days early.
    """
    truncated = timestamp.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    following = _next_month(truncated)
    return following if (following - timestamp) < (timestamp - truncated) else truncated


# Any date will do: it exists only to ask bar_end() how long one candle of a
# given timeframe lasts, so nothing below keeps a duration table of its own
# to drift out of step with the one above.
_DURATION_ANCHOR = datetime(2000, 1, 1, tzinfo=UTC)


def bars_from_closes(
    symbol: str, timeframe: Timeframe, closes: list[float], ending_at: datetime | None = None
) -> list[Bar]:
    """A settled candle series with the given closing prices, oldest first.

    Every candle ends at or before `ending_at` (now by default), so nothing
    built here is the half-formed candle a strategy must never see. Shared by
    the mock provider and by strategy validation rather than reimplemented in
    each: both need believable candles, neither should own its own arithmetic
    for how long one lasts.
    """
    if not closes:
        return []

    step = bar_end(_DURATION_ANCHOR, timeframe) - _DURATION_ANCHOR
    newest_open = (ending_at or datetime.now(UTC)) - step
    oldest_open = newest_open - step * (len(closes) - 1)

    bars: list[Bar] = []
    for age, close in enumerate(closes):
        # Each candle opens where the previous one closed, which is what an
        # unbroken session looks like and keeps gap-detection logic honest.
        open_ = closes[age - 1] if age else close
        bars.append(
            Bar(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=oldest_open + step * age,
                open=open_,
                high=round(max(open_, close) * 1.002, 4),
                low=round(min(open_, close) * 0.998, 4),
                close=close,
                volume=1000.0,
            )
        )
    return bars


def closed_bars(
    bars: list[Bar],
    now: datetime | None = None,
    data_source: DataSource | None = None,
) -> list[Bar]:
    """Drop the candle that is still being built.

    Providers append the in-progress candle to the end of the history and
    update it on every request, so only the last row can be partial.

    INTRADAY CANDLES ARE CLAMPED TO THE SESSION, when the caller says which
    source the symbol came from. Yahoo aligns intraday candles to each
    exchange's own open, and a session rarely divides evenly:
        AAPL     4h candles at 09:30 and 13:30 New York -- the second is 2.5h
        0050.TW  4h candles at 09:00 and 13:00 Taipei   -- the second is 30 MIN
    Measured against the real API, not assumed. bar_end() adds a flat four
    hours because it is deliberately calendar arithmetic -- backtests use it as
    a step size and must keep that -- so without this clamp the candle that
    finished for good at 13:30 Taipei is withheld until 17:00, and a 「4 小時線
    收盤」 alert fires three and a half hours late. 1h has always had a
    thirty-minute version of the same lateness.

    Only INTRADAY, and only when a session is known. A daily candle is final
    when the DAY ends, not when the session does: releasing it at 13:30 would
    hand a strategy a candle Yahoo may still adjust after hours. A market with
    no session (crypto) keeps the plain arithmetic -- 4h divides 24h exactly,
    so there is no short candle, and clamping to a session that does not exist
    would release one that is still moving.
    """
    if not bars:
        return []
    now = now or datetime.now(UTC)
    last = bars[-1]
    return bars[:-1] if _bar_end_in_session(last, data_source) > now else bars


# Intraday only. Anything a whole day or longer is final when its calendar
# period ends, whatever the exchange did in between.
_INTRADAY = frozenset(
    {
        Timeframe.MINUTE_1,
        Timeframe.MINUTE_5,
        Timeframe.MINUTE_15,
        Timeframe.MINUTE_30,
        Timeframe.HOUR_1,
        Timeframe.HOUR_4,
        Timeframe.HOUR_12,
    }
)


def _bar_end_in_session(bar: Bar, data_source: DataSource | None) -> datetime:
    """When this candle really stops moving, session close included."""
    plain = bar_end(bar.timestamp, bar.timeframe)
    if data_source is None or bar.timeframe not in _INTRADAY:
        return plain

    # Imported here: market_calendar reads the symbol rules, which import from
    # this module.
    from app.services.market_calendar import session_close_after

    close = session_close_after(bar.symbol, data_source, bar.timestamp)
    return min(plain, close) if close is not None else plain


# Binance quote assets this app deals in, longest first so USDT is matched
# before USDC's shorter neighbours and BTC does not swallow the tail of a
# pair that ends in something longer.
_CRYPTO_QUOTE_ASSETS = ("USDT", "USDC", "TWD", "ETH", "BTC")


def currency_for(symbol: str, data_source: DataSource) -> str | None:
    """The currency a symbol is quoted in, from the symbol itself.

    DERIVED, NOT GUESSED. A .TW symbol is quoted in TWD by definition of the
    market that suffix names -- this is not an inference about the instrument,
    it is what the suffix means. Same for a Binance pair, whose quote asset is
    literally the tail of its own name.

    None for anything else, including markets this app does not model. Claiming
    a currency for a .HK symbol would be the confident wrong answer the whole
    symbol effort exists to avoid, and a symbol this app cannot price does not
    need one.

    Used as a fallback: a provider that reports its own currency is believed
    first. yfinance's fast_info carries it and costs nothing extra, so the
    five-second poll gets the real answer rather than this one.
    """
    text = (symbol or "").strip().upper()
    if not text:
        return None
    # A company name typed in Chinese has no dot and no dash either, and the
    # bare-ticker rule below would hand it "USD". Nothing in this app can price
    # it, so the honest answer is that it has no currency.
    if not text.isascii():
        return None

    if data_source == DataSource.BINANCE:
        for asset in _CRYPTO_QUOTE_ASSETS:
            if text.endswith(asset) and len(text) > len(asset):
                return asset
        return None

    if text.endswith((".TW", ".TWO")):
        return "TWD"
    if "." not in text and "-" not in text:
        # A bare ticker is a US listing everywhere else in this app -- see
        # services/market_calendar.py, which classifies the same way.
        return "USD"
    # BRK-B and friends: still a US listing.
    if "." not in text:
        return "USD"
    return None
