import logging
import time
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.models.enums import DataSource
from app.models.market import MarketQuote
from app.models.mixins import utcnow
from app.services.market_data.base import Bar, Quote, QuoteProvider, Timeframe, closed_bars
from app.services.market_data.providers.binance_provider import BinanceProvider
from app.services.market_data.providers.yfinance_provider import YFinanceProvider

# yfinance is an unofficial scraper -- polling every symbol at the app's raw
# MARKET_DATA_POLL_INTERVAL_SEC cadence would get an IP rate-limited or
# blocked within hours. Cache each provider's responses briefly instead.
_DEFAULT_TTL_SEC: dict[DataSource, float] = {
    DataSource.YFINANCE: 15.0,
    DataSource.BINANCE: 5.0,
}

# Candle history is the far more expensive request -- years of rows per
# symbol -- and a candle that has closed can never change again, so re-asking
# for it on every poll is pure waste aimed straight at the rate limiter.
# Each TTL is a fraction of its own candle, which is the honest bound: it is
# short enough that a newly closed candle is picked up promptly, and long
# enough that nothing is downloaded faster than it can possibly change.
_DEFAULT_BAR_TTL_SEC: dict[Timeframe, float] = {
    Timeframe.MINUTE_1: 30.0,
    Timeframe.MINUTE_5: 60.0,
    Timeframe.MINUTE_15: 120.0,
    Timeframe.HOUR_1: 300.0,
    Timeframe.DAY_1: 900.0,
    Timeframe.WEEK_1: 3600.0,
    Timeframe.MONTH_1: 3600.0,
}

# Enough history for a 200-period indicator to warm up, with room to spare,
# and few enough rows that replaying them through a sandboxed strategy on
# startup costs milliseconds.
DEFAULT_BAR_LIMIT = 300


logger = logging.getLogger("app.market_data")


class MarketDataService:
    def __init__(
        self,
        providers: dict[DataSource, QuoteProvider] | None = None,
        ttl_sec: dict[DataSource, float] | None = None,
        bar_ttl_sec: dict[Timeframe, float] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._providers = providers or {
            DataSource.YFINANCE: YFinanceProvider(),
            DataSource.BINANCE: BinanceProvider(),
        }
        self._ttl_sec = {**_DEFAULT_TTL_SEC, **(ttl_sec or {})}
        self._bar_ttl_sec = {**_DEFAULT_BAR_TTL_SEC, **(bar_ttl_sec or {})}
        self._clock = clock
        self._cache: dict[DataSource, tuple[float, dict[str, Quote]]] = {}
        # Keyed per symbol and per timeframe, unlike the quote cache's single
        # per-source bucket. That is what keeps one symbol's fetch schedule
        # entirely its own business -- see get_bars(). The stored tuple is
        # (fetched_at, limit_asked_for, bars).
        self._bar_cache: dict[tuple[DataSource, str, Timeframe], tuple[float, int, list[Bar]]] = {}

    def get_quotes(self, symbols: list[str], data_source: DataSource) -> dict[str, Quote]:
        if not symbols:
            return {}

        now = self._clock()
        cached_at, cached_quotes = self._cache.get(data_source, (0.0, {}))
        stale = (now - cached_at) > self._ttl_sec.get(data_source, 5.0)
        missing = [s for s in symbols if s not in cached_quotes]

        if stale or missing:
            provider = self._providers[data_source]
            fetch_list = symbols if stale else missing
            fresh = provider.get_quotes(fetch_list)
            cached_quotes = {**cached_quotes, **fresh}
            # Only a full refresh restarts the TTL clock. A backfill must not:
            # providers silently omit symbols they can't resolve (a typo, a
            # delisting, a Taiwan ticker missing its .TW suffix), so that
            # symbol stays permanently "missing" and gets re-fetched every
            # poll. Stamping `now` there re-armed the timer forever, `stale`
            # never came true again, and every OTHER symbol's price froze at
            # its first value -- silently, behind a fresh-looking timestamp,
            # with stop-loss/take-profit comparing against a price that could
            # no longer move.
            self._cache[data_source] = (now if stale else cached_at, cached_quotes)

            # Providers omit what they cannot resolve rather than raising, so
            # a blocked IP, a renamed API field and a mistyped ticker all
            # arrive here as the same quiet gap. Naming the missing symbols is
            # the difference between "the feed broke at 14:05" and the owner
            # noticing days later that no orders ever appeared.
            missing_now = [s for s in fetch_list if s not in fresh]
            if missing_now:
                logger.warning(
                    "%s returned no quote for %s (asked for %s)",
                    data_source.value,
                    ", ".join(sorted(missing_now)),
                    len(fetch_list),
                )

        return {s: cached_quotes[s] for s in symbols if s in cached_quotes}

    def get_bars(
        self,
        symbol: str,
        timeframe: Timeframe,
        data_source: DataSource,
        limit: int = DEFAULT_BAR_LIMIT,
    ) -> list[Bar]:
        """Closed candles for one symbol, newest last, served from cache
        between refreshes.

        The cache key includes the symbol, which is the lesson the quote
        cache above paid for: with one shared bucket, a symbol the provider
        could never resolve dragged every other symbol's refresh schedule
        around with it. Here a dead symbol only ever wastes its own slot.

        The depth an entry was fetched at is remembered alongside it, because
        a shallower cached window cannot answer a deeper question. The market
        loop only ever asks for DEFAULT_BAR_LIMIT candles; a backtest over
        several years asks for thousands, and serving it the loop's 300 would
        silently shorten the range the owner asked to test while reporting
        success. A *smaller* limit is still served from cache -- that is the
        common case and the one the rate limiter cares about.
        """
        key = (data_source, symbol, timeframe)
        now = self._clock()
        cached_at, cached_limit, cached = self._bar_cache.get(key, (None, 0, []))
        fresh = cached_at is not None and (now - cached_at) <= self._bar_ttl_sec[timeframe]
        if fresh and cached_limit >= limit:
            return cached[-limit:]

        fetched = closed_bars(self._providers[data_source].get_bars(symbol, timeframe, limit))
        # An empty result is a fetch that happened, so it stamps the clock:
        # otherwise a symbol the provider cannot resolve is re-requested on
        # every single poll, which is exactly how an IP gets blocked. Keeping
        # the previous history rather than replacing it with nothing also
        # stops one failed request from looking like "this strategy has no
        # history yet" and silently restarting its warm-up.
        bars = fetched or cached
        # Stamped with the limit just asked for even when the fetch came back
        # empty: it records what was requested, so a repeat of the same
        # request is served from cache rather than hammering a symbol the
        # provider cannot resolve.
        self._bar_cache[key] = (now, limit, bars)
        return bars[-limit:]

    def upsert_quotes(self, db: Session, quotes: dict[str, Quote]) -> None:
        for symbol, quote in quotes.items():
            row = db.get(MarketQuote, symbol)
            if row is None:
                row = MarketQuote(symbol=symbol, data_source=quote.data_source, price=quote.price)
                db.add(row)
            row.data_source = quote.data_source
            row.price = quote.price
            row.prev_close = quote.prev_close
            row.change_pct = quote.change_pct
            row.volume = quote.volume
            row.quote_time = quote.quote_time
            row.fetched_at = utcnow()
        db.commit()


_default_service = MarketDataService()


def get_market_data_service() -> MarketDataService:
    return _default_service
