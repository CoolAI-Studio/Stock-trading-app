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
        first = timestamp.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return (
            first.replace(year=first.year + 1, month=1)
            if first.month == 12
            else first.replace(month=first.month + 1)
        )
    return timestamp + _FIXED_DURATION[timeframe]


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
