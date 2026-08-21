import logging
import time
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.models.enums import DataSource
from app.models.market import MarketQuote
from app.models.mixins import utcnow
from app.services.market_data.base import (
    Bar,
    BarFetchError,
    Quote,
    QuoteProvider,
    Timeframe,
    closed_bars,
)
from app.services.market_data.providers.binance_provider import BinanceProvider
from app.services.market_data.providers.yfinance_provider import YFinanceProvider

# yfinance is an unofficial scraper -- polling every symbol at the app's raw
# MARKET_DATA_POLL_INTERVAL_SEC cadence would get an IP rate-limited or
# blocked within hours. Cache each provider's responses briefly instead.
_DEFAULT_TTL_SEC: dict[DataSource, float] = {
    DataSource.YFINANCE: 15.0,
    DataSource.BINANCE: 5.0,
}

# How long a quote may go on being served after the provider stopped
# confirming it. Two things are true at once and this number is where they
# meet: a one-poll hiccup must not open a gap, because no quote means no
# evaluation and a skipped evaluation is a missed alert -- while an indefinite
# failure must not be served as a live price, because every threshold in the
# app is then comparing against a number that can no longer move.
#
# Comfortably more than a handful of failed refreshes at each source's own TTL,
# and far less than the forever it used to be. Binance is shorter because it
# trades around the clock: a two-minute-old crypto price is already a
# different market, whereas an equity quote outside session hours legitimately
# does not change.
_DEFAULT_STALE_LIMIT_SEC: dict[DataSource, float] = {
    DataSource.YFINANCE: 300.0,
    DataSource.BINANCE: 120.0,
}

# Candle history is the far more expensive request -- years of rows per
# symbol -- and a candle that has closed can never change again, so re-asking
# for it on every poll is pure waste aimed straight at the rate limiter.
# Each TTL is a fraction of its own candle, which is the honest bound: it is
# short enough that a newly closed candle is picked up promptly, and long
# enough that nothing is downloaded faster than it can possibly change.
# How long a FAILED bar fetch holds the door shut. Short on purpose: it exists
# to stop a refetch-on-focus page turning one 429 into a hundred, not to decide
# how long a symbol has no history. Fifteen minutes of that was the bug.
FAILED_FETCH_RETRY_SEC = 60.0

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
        stale_limit_sec: dict[DataSource, float] | None = None,
    ) -> None:
        self._providers = providers or {
            DataSource.YFINANCE: YFinanceProvider(),
            DataSource.BINANCE: BinanceProvider(),
        }
        self._ttl_sec = {**_DEFAULT_TTL_SEC, **(ttl_sec or {})}
        self._bar_ttl_sec = {**_DEFAULT_BAR_TTL_SEC, **(bar_ttl_sec or {})}
        self._stale_limit_sec = {**_DEFAULT_STALE_LIMIT_SEC, **(stale_limit_sec or {})}
        self._clock = clock
        # When each symbol was last actually ANSWERED FOR, monotonic. Kept
        # apart from the cache's single per-source timestamp, which records
        # when the bucket was last refreshed as a whole and therefore says
        # nothing about the one symbol inside it that stopped coming back.
        self._answered_at: dict[tuple[DataSource, str], float] = {}
        self._cache: dict[DataSource, tuple[float, dict[str, Quote]]] = {}
        # Keyed per symbol and per timeframe, unlike the quote cache's single
        # per-source bucket. That is what keeps one symbol's fetch schedule
        # entirely its own business -- see get_bars(). The stored tuple is
        # (fetched_at, limit_asked_for, bars).
        self._bar_cache: dict[tuple[DataSource, str, Timeframe], tuple[float, int, list[Bar]]] = {}
        # When a bar fetch last FAILED for each key, monotonic. Separate from
        # the cache above because the two answer different questions: that one
        # holds what is known, this one holds when it was last impossible to
        # find out.
        self._bar_failed_at: dict[tuple[DataSource, str, Timeframe], float] = {}
        # Whether the most recent get_bars call could not reach the provider.
        # Read by the chart endpoint so 「we could not ask」 and 「there is no
        # history」 reach the screen as different sentences: one clears on its
        # own and one never will.
        self.last_bar_fetch_failed = False

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
            # Stamped here, not in the providers: this is the moment the
            # answer arrived, and it has to survive being served from cache
            # later or a held price is indistinguishable from a live one.
            answered_at = utcnow()
            for quote in fresh.values():
                quote.fetched_at = answered_at
            for symbol in fresh:
                self._answered_at[(data_source, symbol)] = now
            cached_quotes = {**cached_quotes, **fresh}

            # Withdraw what the provider has now gone too long without
            # confirming. Without this the merge above preserved a dead
            # symbol's last entry forever -- a full refresh could not dislodge
            # it, because a refresh asks for everything and the merge keeps
            # whatever the answer omitted. Only symbols just asked for are
            # considered; another caller's names are not this fetch's business.
            limit = self._stale_limit_sec.get(data_source, 300.0)
            for symbol in fetch_list:
                if symbol in fresh:
                    continue
                last_ok = self._answered_at.get((data_source, symbol))
                if last_ok is None or (now - last_ok) > limit:
                    cached_quotes.pop(symbol, None)
                    self._answered_at.pop((data_source, symbol), None)
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

        # A recent failure holds the door shut, briefly. Without this a page
        # that refetches on focus turns one rate-limited response into a
        # hundred; with a full TTL it turns one into fifteen minutes of a
        # perfectly good symbol reading as delisted.
        failed_at = self._bar_failed_at.get(key)
        if failed_at is not None and (now - failed_at) <= FAILED_FETCH_RETRY_SEC:
            self.last_bar_fetch_failed = True
            return cached[-limit:]
        self.last_bar_fetch_failed = False

        try:
            fetched = closed_bars(self._providers[data_source].get_bars(symbol, timeframe, limit))
        except BarFetchError as exc:
            # A FAILURE IS NOT AN ANSWER, and this line is the whole reason the
            # bug existed. The old code could not tell 「asked, and there is
            # nothing here」 from 「could not ask」, so one 429 on a shared
            # deployment IP was stored as fact and a stock with fifty years of
            # history read as having none for the next fifteen minutes.
            #
            # Still not retried on every request, because the concern the old
            # comment raised is real -- a page that refetches on focus would
            # turn one 429 into a hundred. It waits FAILED_FETCH_RETRY_SEC
            # instead of a full TTL: a transient failure costs a minute, not
            # the fifteen a real answer is worth.
            logger.warning("%s bars failed for %s: %s", data_source.value, symbol, exc)
            self._bar_failed_at[key] = now
            # Set HERE as well as in the cooldown branch above: the very first
            # failure is the one somebody is looking at, and without this the
            # page would call it 「no history」 exactly once -- on the request
            # that actually failed.
            self.last_bar_fetch_failed = True
            # Whatever history is already held still stands: one failed request
            # must not look like 「this strategy has no history yet」 and
            # silently restart its warm-up.
            return cached[-limit:]

        # An empty ANSWER is a fetch that happened, so it stamps the clock:
        # otherwise a symbol the provider genuinely cannot resolve is
        # re-requested on every single poll, which is exactly how an IP gets
        # blocked. Keeping the previous history rather than replacing it with
        # nothing also stops one thin window from restarting a warm-up.
        bars = fetched or cached
        # Stamped with the limit just asked for even when the answer was empty:
        # it records what was requested, so a repeat of the same request is
        # served from cache rather than hammering a symbol that has nothing.
        self._bar_cache[key] = (now, limit, bars)
        self._bar_failed_at.pop(key, None)
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
            # Never overwrite a known currency with nothing: a provider that
            # stops reporting it for one poll must not erase the label the
            # screen is using.
            if quote.currency:
                row.currency = quote.currency
            # The quote's own arrival time when it has one. Re-stamping
            # 「now」 on every poll is what let a price frozen at 09:00 read as
            # seconds old at 14:00 on the page built to reveal exactly that.
            row.fetched_at = quote.fetched_at or utcnow()
        db.commit()


_default_service = MarketDataService()


def get_market_data_service() -> MarketDataService:
    return _default_service
