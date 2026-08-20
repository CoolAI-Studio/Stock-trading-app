from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from app.models.enums import DataSource


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
    HOUR_1 = "1h"
    DAY_1 = "1d"
    WEEK_1 = "1wk"
    MONTH_1 = "1mo"


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
    Timeframe.HOUR_1: timedelta(hours=1),
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


def closed_bars(bars: list[Bar], now: datetime | None = None) -> list[Bar]:
    """Drop the candle that is still being built.

    Providers append the in-progress candle to the end of the history and
    update it on every request, so only the last row can be partial.
    """
    if not bars:
        return []
    now = now or datetime.now(UTC)
    last = bars[-1]
    return bars[:-1] if bar_end(last.timestamp, last.timeframe) > now else bars


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
