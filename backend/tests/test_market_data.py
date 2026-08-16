from app.models.enums import DataSource
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
