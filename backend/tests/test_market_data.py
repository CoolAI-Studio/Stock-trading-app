from decimal import Decimal

from app.models.enums import DataSource
from app.services.market_data.base import Quote
from app.services.market_data.providers.mock_provider import MockProvider
from app.services.market_data.service import MarketDataService


def test_get_quotes_returns_a_quote_per_symbol():
    provider = MockProvider(base_prices={"AAPL": 100.0, "TSLA": 200.0})
    service = MarketDataService(providers={DataSource.YFINANCE: provider})

    quotes = service.get_quotes(["AAPL", "TSLA"], DataSource.YFINANCE)

    assert set(quotes) == {"AAPL", "TSLA"}
    assert quotes["AAPL"].symbol == "AAPL"
    assert quotes["AAPL"].data_source == DataSource.YFINANCE


def test_quotes_are_cached_within_ttl():
    provider = MockProvider(base_prices={"AAPL": 100.0})
    calls = []
    original_get_quotes = provider.get_quotes

    def counting_get_quotes(symbols):
        calls.append(list(symbols))
        return original_get_quotes(symbols)

    provider.get_quotes = counting_get_quotes

    fake_time = {"t": 0.0}
    service = MarketDataService(
        providers={DataSource.YFINANCE: provider},
        ttl_sec={DataSource.YFINANCE: 15.0},
        clock=lambda: fake_time["t"],
    )

    service.get_quotes(["AAPL"], DataSource.YFINANCE)
    fake_time["t"] += 5.0
    service.get_quotes(["AAPL"], DataSource.YFINANCE)  # still within TTL

    assert len(calls) == 1


def test_quotes_refetch_after_ttl_expires():
    provider = MockProvider(base_prices={"AAPL": 100.0})
    calls = []
    original_get_quotes = provider.get_quotes

    def counting_get_quotes(symbols):
        calls.append(list(symbols))
        return original_get_quotes(symbols)

    provider.get_quotes = counting_get_quotes

    fake_time = {"t": 0.0}
    service = MarketDataService(
        providers={DataSource.YFINANCE: provider},
        ttl_sec={DataSource.YFINANCE: 15.0},
        clock=lambda: fake_time["t"],
    )

    service.get_quotes(["AAPL"], DataSource.YFINANCE)
    fake_time["t"] += 20.0
    service.get_quotes(["AAPL"], DataSource.YFINANCE)  # past TTL

    assert len(calls) == 2


def test_upsert_quotes_persists_to_db(db_session):
    from app.models.market import MarketQuote

    provider = MockProvider(base_prices={"AAPL": 100.0})
    service = MarketDataService(providers={DataSource.YFINANCE: provider})

    quotes = service.get_quotes(["AAPL"], DataSource.YFINANCE)
    service.upsert_quotes(db_session, quotes)

    row = db_session.get(MarketQuote, "AAPL")
    assert row is not None
    assert row.price == quotes["AAPL"].price


class _OmitsUnknownProvider:
    """Returns quotes only for symbols it recognises, silently dropping the
    rest -- which is exactly what yfinance does for a delisted ticker, a typo,
    or a Taiwan symbol missing its `.TW` suffix.

    MockProvider now drops what it cannot price too, so it would cover the
    omission itself; what it cannot do is record WHICH symbols each fetch
    asked for, and the cache-behaviour tests below turn on exactly that.
    """

    data_source = DataSource.YFINANCE

    def __init__(self, known: str, start_price: float = 100.0) -> None:
        self._known = known
        self._price = start_price
        self.calls: list[list[str]] = []

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        self.calls.append(list(symbols))
        self._price += 1.0
        return {
            s: Quote(symbol=s, data_source=self.data_source, price=Decimal(str(self._price)))
            for s in symbols
            if s == self._known
        }


def test_an_unresolvable_symbol_does_not_freeze_every_other_price():
    """Regression, and the most expensive bug this suite guards: backfilling a
    symbol the provider never returns used to rewrite the cache timestamp on
    every single poll. `stale` therefore never came true again, so every OTHER
    symbol's price froze at whatever it was on the first tick -- silently, with
    a fresh-looking timestamp -- and stop-loss/take-profit stopped firing for
    the entire portfolio because the price they compare against never moved.
    """
    provider = _OmitsUnknownProvider(known="AAPL", start_price=100.0)
    fake_time = {"t": 1000.0}
    service = MarketDataService(
        providers={DataSource.YFINANCE: provider},
        ttl_sec={DataSource.YFINANCE: 15.0},
        clock=lambda: fake_time["t"],
    )

    watched = ["AAPL", "DELISTED.OLD"]
    first = service.get_quotes(watched, DataSource.YFINANCE)
    assert "AAPL" in first
    assert "DELISTED.OLD" not in first
    first_price = first["AAPL"].price

    # Poll well past the TTL, the way the worker loop does every few seconds.
    for _ in range(10):
        fake_time["t"] += 5.0
        latest = service.get_quotes(watched, DataSource.YFINANCE)

    assert latest["AAPL"].price > first_price, (
        "AAPL's price never moved: the unresolvable symbol kept re-arming the "
        "cache TTL, so the full refresh never happened again"
    )


def test_backfilling_a_missing_symbol_does_not_extend_the_ttl():
    """The mechanism behind the bug above, asserted directly: a partial fetch
    for a newly-watched symbol must leave the existing TTL clock alone."""
    provider = _OmitsUnknownProvider(known="AAPL")
    fake_time = {"t": 1000.0}
    service = MarketDataService(
        providers={DataSource.YFINANCE: provider},
        ttl_sec={DataSource.YFINANCE: 15.0},
        clock=lambda: fake_time["t"],
    )

    service.get_quotes(["AAPL"], DataSource.YFINANCE)
    assert provider.calls == [["AAPL"]]

    # A backfill for an unknown symbol 10s in...
    fake_time["t"] += 10.0
    service.get_quotes(["AAPL", "DELISTED.OLD"], DataSource.YFINANCE)

    # ...must not push the full-refresh deadline out past the original 15s.
    fake_time["t"] += 6.0
    service.get_quotes(["AAPL", "DELISTED.OLD"], DataSource.YFINANCE)

    assert ["AAPL", "DELISTED.OLD"] in provider.calls, (
        "no full refresh happened after the TTL elapsed"
    )
