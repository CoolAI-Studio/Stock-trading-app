import time
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.models.enums import DataSource
from app.models.market import MarketQuote
from app.models.mixins import utcnow
from app.services.market_data.base import Quote, QuoteProvider
from app.services.market_data.providers.binance_provider import BinanceProvider
from app.services.market_data.providers.yfinance_provider import YFinanceProvider

# yfinance is an unofficial scraper -- polling every symbol at the app's raw
# MARKET_DATA_POLL_INTERVAL_SEC cadence would get an IP rate-limited or
# blocked within hours. Cache each provider's responses briefly instead.
_DEFAULT_TTL_SEC: dict[DataSource, float] = {
    DataSource.YFINANCE: 15.0,
    DataSource.BINANCE: 5.0,
}


class MarketDataService:
    def __init__(
        self,
        providers: dict[DataSource, QuoteProvider] | None = None,
        ttl_sec: dict[DataSource, float] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._providers = providers or {
            DataSource.YFINANCE: YFinanceProvider(),
            DataSource.BINANCE: BinanceProvider(),
        }
        self._ttl_sec = {**_DEFAULT_TTL_SEC, **(ttl_sec or {})}
        self._clock = clock
        self._cache: dict[DataSource, tuple[float, dict[str, Quote]]] = {}

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

        return {s: cached_quotes[s] for s in symbols if s in cached_quotes}

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
