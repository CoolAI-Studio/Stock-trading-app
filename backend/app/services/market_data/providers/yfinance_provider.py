import logging
import math
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import yfinance as yf

from app.models.enums import DataSource
from app.services.market_data.base import Bar, BarFetchError, Quote, Timeframe, currency_for

# How far back to ask for each interval. Yahoo caps intraday history hard --
# roughly 7 days of 1m, 60 days of anything else under an hour, 2 years of
# hourly -- and asking beyond the cap returns an empty frame rather than a
# shorter one, so these are ceilings, not preferences. The daily-and-slower
# windows are simply generous enough that a 200-period indicator warms up.
_PERIOD_FOR: dict[Timeframe, str] = {
    Timeframe.MINUTE_1: "5d",
    Timeframe.MINUTE_5: "60d",
    Timeframe.MINUTE_15: "60d",
    Timeframe.HOUR_1: "730d",
    Timeframe.DAY_1: "5y",
    Timeframe.WEEK_1: "10y",
    Timeframe.MONTH_1: "max",
}


logger = logging.getLogger("app.market_data.yfinance")


class YFinanceProvider:
    data_source = DataSource.YFINANCE

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        if not symbols:
            return {}

        quotes: dict[str, Quote] = {}
        for symbol in symbols:
            try:
                fast_info = yf.Ticker(symbol).fast_info
                price = fast_info["lastPrice"]
                prev_close = fast_info.get("previousClose") if hasattr(fast_info, "get") else None
                # fast_info already carries it, so the poll pays nothing extra.
                # Falling back to the symbol rather than to None: losing the
                # currency entirely is worse than reading it off a suffix that
                # defines it.
                currency = getattr(fast_info, "currency", None) or currency_for(
                    symbol, self.data_source
                )
            except Exception:
                # Carrying on is right -- one bad symbol must not stop the
                # whole poll -- but until now this `continue` was also the
                # only record that anything had gone wrong, so a blocked IP
                # and a quiet market were indistinguishable in the logs
                # because there were no logs.
                logger.warning("yfinance quote failed for %s", symbol, exc_info=True)
                continue

            if price is None:
                continue

            try:
                # yfinance returns raw floats with binary-precision noise
                # (e.g. 305.92999267578125) -- round before persisting/
                # displaying rather than storing that noise verbatim.
                price_dec = Decimal(str(round(price, 4)))
                prev_close_dec = Decimal(str(round(prev_close, 4))) if prev_close else None
            except InvalidOperation:
                continue

            change_pct = None
            if prev_close_dec:
                change_pct = round(((price_dec - prev_close_dec) / prev_close_dec) * 100, 4)

            quotes[symbol] = Quote(
                symbol=symbol,
                data_source=self.data_source,
                price=price_dec,
                prev_close=prev_close_dec,
                change_pct=change_pct,
                # NOT datetime.now(). fast_info carries no temporal field at
                # all -- currency, day_high, last_price and so on, nothing
                # about when the trade happened -- and filling it with our own
                # clock made every price look current, including the final
                # close of a stock delisted years ago. `quote_time` is the one
                # field built to reveal staleness (see schemas/position.py),
                # and a fabricated value guaranteed it never could.
                # `fetched_at` has always recorded when WE asked, and
                # positions.py already falls back to it.
                quote_time=None,
                currency=currency,
            )
        return quotes

    def get_bars(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Bar]:
        try:
            frame = yf.Ticker(symbol).history(
                period=_PERIOD_FOR[timeframe],
                interval=timeframe.value,
                auto_adjust=True,
            )
        except Exception as exc:
            # NOT the same contract as get_quotes any more. A quote that fails
            # is absent for one poll and the next one fixes it; a bar fetch
            # that fails gets CACHED, and returning [] here made the service
            # store 「this symbol has no history」 as fact for fifteen minutes.
            #
            # yfinance re-raises YFRateLimitError unconditionally -- the one
            # error it does not hide -- and that is exactly the shared-IP 429
            # a free-tier deployment meets.
            logger.warning("yfinance bars failed for %s", symbol, exc_info=True)
            raise BarFetchError(f"{symbol}: {type(exc).__name__}") from exc

        if frame is None or frame.empty:
            # ALSO a failure, and the likelier of the two. yfinance 1.6.0 ships
            # `hide_exceptions = True`, so history() catches its own errors and
            # hands back an empty frame rather than raising -- meaning the
            # commonest way a fetch fails arrives here, not in the except
            # above. A symbol that genuinely has no candles is rare enough,
            # and recoverable enough, to be worth calling a failure: the cost
            # of being wrong is one short retry, against fifteen minutes of a
            # real stock reading as delisted.
            logger.warning("yfinance returned an empty frame for %s", symbol)
            raise BarFetchError(f"{symbol}: yfinance returned nothing")

        bars: list[Bar] = []
        for index, row in frame.tail(limit).iterrows():
            values = [_as_float(row.get(field)) for field in ("Open", "High", "Low", "Close")]
            if any(value is None for value in values):
                # Yahoo pads gaps (halts, holidays it got wrong) with NaN
                # rows. A NaN reaching an indicator poisons every later value
                # it feeds, so the row is dropped instead.
                continue
            open_, high, low, close = values
            bars.append(
                Bar(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=_as_utc(index),
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=_as_float(row.get("Volume")),
                )
            )
        return bars


def _as_float(value) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    # Round for the same reason quotes are rounded: yfinance hands back
    # binary-precision noise, and a strategy comparing two "equal" closes
    # should not lose on the fifteenth decimal.
    return None if math.isnan(result) else round(result, 4)


def _as_utc(timestamp) -> datetime:
    value = timestamp.to_pydatetime() if hasattr(timestamp, "to_pydatetime") else timestamp
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
