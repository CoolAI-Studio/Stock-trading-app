import logging
import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

import httpx
import yfinance as yf

from app.models.enums import DataSource
from app.services.market_data.base import Bar, BarFetchError, Quote, Timeframe, currency_for

# HOW FAR BACK TO ASK, AS A FUNCTION OF HOW MUCH WAS ASKED FOR.
#
# It used to be a fixed table -- daily was "5y" -- and `limit` was ignored
# entirely: the frame came back at whatever depth the table said, then
# `.tail(limit)` trimmed it. So a backtest asking for 2,000 daily candles got
# five years and no indication that it had been shortened, and a range that
# started before that window came back empty and was blamed on the symbol.
#
# A bigger constant would only move the day it happens. The depth has to be a
# function of the request, which is exactly the lesson service.py already paid
# for one layer up: 「a shallower cached window cannot answer a deeper
# question」.

# Yahoo's hard walls. Past these it returns an EMPTY frame rather than a
# shorter one, so asking for one day more turns data into no data. Measured,
# not guessed: roughly 7 days of 1m, 60 days of anything else under an hour,
# 2 years of hourly. 4h shares the hourly wall (and must never be asked as
# "max": yfinance's max branch only recognises a fixed interval list, 4h is
# not on it, and the fallthrough gives 168 candles for AAPL).
_MAX_DAYS: dict[Timeframe, int | None] = {
    Timeframe.MINUTE_1: 7,
    Timeframe.MINUTE_5: 60,
    Timeframe.MINUTE_15: 60,
    Timeframe.MINUTE_30: 60,
    Timeframe.HOUR_1: 730,
    Timeframe.HOUR_4: 730,
    # No wall worth clamping to: daily and slower go back decades.
    Timeframe.DAY_1: None,
    Timeframe.WEEK_1: None,
    Timeframe.MONTH_1: None,
}

# Calendar days one candle of each timeframe covers, generously. Daily is 1.5
# rather than 1 because a week holds five trading days, not seven, and a year
# holds about 252 -- asking for exactly `limit` days would come up a fifth
# short before a single holiday.
_CALENDAR_DAYS_PER_CANDLE: dict[Timeframe, float] = {
    Timeframe.MINUTE_1: 1 / 300,
    Timeframe.MINUTE_5: 1 / 60,
    Timeframe.MINUTE_15: 1 / 20,
    Timeframe.MINUTE_30: 1 / 10,
    Timeframe.HOUR_1: 1 / 5,
    Timeframe.HOUR_4: 1.0,
    Timeframe.DAY_1: 1.5,
    Timeframe.WEEK_1: 7.5,
    Timeframe.MONTH_1: 32.0,
}

# A floor, so a small request still warms up a 200-period indicator and still
# covers a long weekend.
_MIN_DAYS = 30


def _period_days(timeframe: Timeframe, limit: int) -> int:
    """How many calendar days of history to ask for, for `limit` candles."""
    cap = _MAX_DAYS[timeframe]
    if cap is not None:
        # The wall IS the best answer: anything less is a shorter frame for no
        # reason, anything more is an empty one.
        return cap
    wanted = int(limit * _CALENDAR_DAYS_PER_CANDLE[timeframe]) + 1
    return max(wanted, _MIN_DAYS)


# THE CHART ENDPOINT, ASKED DIRECTLY.
#
# MEASURED from a datacentre IP during a diagnosis, at the same moment:
#
#     getcrumb                            -> 429
#     /v8/finance/chart/AAPL + Chrome UA  -> 200, with real data
#
# WHAT IS BLOCKED IS THE HEADER, NOT THE IP. yfinance has to do a crumb
# handshake first, and that handshake is the part that gets refused -- so a
# free-tier deployment on a shared address loses every quote at once, and
# losing quotes means alerts that do not arrive.
#
# Also measured: the old fast_info path spent THREE requests per symbol per
# poll while bars spent one, so 「quotes are the cheap half」 was backwards.
# Both halves now read the same response.
_CHART_URL = "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

# yfinance's own default is 30 seconds with retries=0. The market loop runs
# every five seconds, and one request hanging holds up that round's stop-loss
# scan and its pending-notification sweep with it.
_HTTP_TIMEOUT_SEC = 10.0

# What a quote needs: the newest daily candle's meta block. A short range keeps
# the response small -- the numbers come from `meta`, not from the candles.
_QUOTE_RANGE = "5d"

# Which timeframes keep the instant they opened. Anything a whole day or longer
# is normalised to local midnight (see _stamp).
_INTRADAY_INTERVALS = frozenset(
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


logger = logging.getLogger("app.market_data.yfinance")


def _fetch_chart(symbol: str, interval: str, range_: str) -> dict | None:
    """The raw chart response, or None if it could not be had or understood.

    None rather than an exception on purpose: every caller has a fallback, and
    Yahoo has changed this payload's shape more than once. 「I could not read
    it」 must not become 「this symbol has no history」, which is the mistake
    services/market_data/service.py already caches against.
    """
    try:
        response = httpx.get(
            _CHART_URL.format(symbol=symbol),
            params={"interval": interval, "range": range_, "includePrePost": "false"},
            headers=_BROWSER_HEADERS,
            timeout=_HTTP_TIMEOUT_SEC,
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.json()
    except Exception:  # noqa: BLE001 -- 任何失敗都只是「這條路走不通」
        logger.warning("chart endpoint failed for %s", symbol, exc_info=True)
        return None


def _result_of(payload: dict | None) -> dict | None:
    try:
        result = payload["chart"]["result"][0]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        return None
    return result if isinstance(result, dict) else None


def _stamp(epoch: int, timeframe: Timeframe, gmtoffset: int) -> datetime:
    """A candle's timestamp, matching what yfinance used to produce.

    INTRADAY keeps the instant it opened -- that is what the 4h session
    alignment in market_data/base.py measures from.

    DAILY AND SLOWER are normalised to local midnight, because yfinance's
    index was a DATE and `bar_end()` adds a flat day to it. Yahoo stamps the
    daily candle at the session open instead (13:30 UTC for AAPL), and copying
    that through would push every daily close 13.5 hours later than before --
    a 「收盤提醒」 arriving half a day late, with nothing on screen to show
    for it.
    """
    moment = datetime.fromtimestamp(epoch, UTC)
    if timeframe in _INTRADAY_INTERVALS:
        return moment
    local = moment + timedelta(seconds=gmtoffset)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - timedelta(seconds=gmtoffset)


def _bars_from_chart(
    payload: dict | None, symbol: str, timeframe: Timeframe, limit: int
) -> list[Bar] | None:
    result = _result_of(payload)
    if result is None:
        return None
    try:
        stamps = result["timestamp"]
        quote = result["indicators"]["quote"][0]
        opens, highs, lows, closes = (
            quote["open"],
            quote["high"],
            quote["low"],
            quote["close"],
        )
        volumes = quote.get("volume") or [None] * len(stamps)
        gmtoffset = int(result["meta"].get("gmtoffset") or 0)
    except (KeyError, IndexError, TypeError):
        return None

    # Split adjustments. MEASURED: `open * (adjclose/close)` matches yfinance's
    # auto_adjust=True to four decimal places (224.6408 vs 224.6409). Without
    # it, the day of a split disagrees with the chart and with every backtest.
    adjusted = None
    try:
        adjusted = result["indicators"]["adjclose"][0]["adjclose"]
    except (KeyError, IndexError, TypeError):
        adjusted = None

    bars: list[Bar] = []
    for i, epoch in enumerate(stamps):
        values = [_as_float(series[i]) for series in (opens, highs, lows, closes)]
        if any(value is None for value in values) or epoch is None:
            # Yahoo pads gaps (halts, holidays it got wrong) with nulls. One
            # null reaching an indicator poisons every later value it feeds.
            continue
        open_, high, low, close = values
        factor = 1.0
        if adjusted is not None:
            adj = _as_float(adjusted[i]) if i < len(adjusted) else None
            if adj is not None and close:
                factor = adj / close
        bars.append(
            Bar(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=_stamp(int(epoch), timeframe, gmtoffset),
                open=round(open_ * factor, 6),
                high=round(high * factor, 6),
                low=round(low * factor, 6),
                close=round(close * factor, 6),
                volume=_as_float(volumes[i]) if i < len(volumes) else None,
            )
        )
    return bars[-limit:] if limit else bars


class YFinanceProvider:
    data_source = DataSource.YFINANCE

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """The latest price per symbol, read off the same chart response.

        MEASURED: the old fast_info path spent three requests per symbol per
        poll, and this loop runs every five seconds -- so quotes, not bars,
        were what earned the shared-IP 429s.
        """
        if not symbols:
            return {}

        quotes: dict[str, Quote] = {}
        for symbol in symbols:
            quote = self._quote_from_chart(symbol)
            if quote is not None:
                quotes[symbol] = quote
                continue
            fallback = self._quote_via_yfinance(symbol)
            if fallback is not None:
                quotes[symbol] = fallback
        return quotes

    def _quote_from_chart(self, symbol: str) -> Quote | None:
        result = _result_of(_fetch_chart(symbol, Timeframe.DAY_1.value, _QUOTE_RANGE))
        if result is None:
            return None
        meta = result.get("meta") or {}
        price = _as_float(meta.get("regularMarketPrice"))
        if price is None:
            return None
        prev_close = _as_float(meta.get("chartPreviousClose"))
        try:
            price_dec = Decimal(str(round(price, 4)))
            prev_close_dec = Decimal(str(round(prev_close, 4))) if prev_close else None
        except InvalidOperation:
            return None

        change_pct = None
        if prev_close_dec:
            change_pct = round(((price_dec - prev_close_dec) / prev_close_dec) * 100, 4)

        # AT LAST A REAL ONE. `quote_time` was forced to None because fast_info
        # carries no temporal field at all, and filling it with our own clock
        # made every price look current -- including the final close of a stock
        # delisted years ago. This field exists to reveal staleness.
        stamped = meta.get("regularMarketTime")
        quote_time = datetime.fromtimestamp(int(stamped), UTC) if stamped else None

        return Quote(
            symbol=symbol,
            data_source=self.data_source,
            price=price_dec,
            prev_close=prev_close_dec,
            change_pct=change_pct,
            quote_time=quote_time,
            currency=meta.get("currency") or currency_for(symbol, self.data_source),
        )

    def _quote_via_yfinance(self, symbol: str) -> Quote | None:
        quotes = self._quotes_via_yfinance([symbol])
        return quotes.get(symbol)

    def _quotes_via_yfinance(self, symbols: list[str]) -> dict[str, Quote]:
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
        """Candles, from the chart endpoint, falling back to yfinance.

        The direct call is first because it is the one that answers on a shared
        IP (see _CHART_URL). The fallback stays because Yahoo has changed this
        payload's shape before, and 「I could not read it」 must not turn into
        「this symbol has no history」.
        """
        parsed = _bars_from_chart(
            _fetch_chart(symbol, timeframe.value, f"{_period_days(timeframe, limit)}d"),
            symbol,
            timeframe,
            limit,
        )
        if parsed:
            return parsed

        return self._bars_via_yfinance(symbol, timeframe, limit)

    def _bars_via_yfinance(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Bar]:
        try:
            frame = yf.Ticker(symbol).history(
                period=f"{_period_days(timeframe, limit)}d",
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
