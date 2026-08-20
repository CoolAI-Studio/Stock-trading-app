"""Which currency a price is in, said out loud everywhere it is shown.

Nothing in the price path carried a currency. Quote, MarketQuote and every API
response were a bare number, and it was safe only by accident: symbol search
could emit .TW/.TWO or a US ticker and nothing else, so the owner supplied the
missing context from memory.

That accident is now gone. app/data/us_aliases.json puts USD instruments in
front of somebody searching in Chinese, and the same company can appear twice
-- 2330.TW at NT$2,375 and TSM at US$300 are both 「台積電」, and both are real
answers to that search. A threshold typed against one and applied to the other
is not an error the app can detect: both are plausible numbers in the same
column.

    「跌破 220」 on TSM means US$220.
    「跌破 220」 on 2330.TW means NT$220, which is a 90% crash.

One of those fires constantly and one never fires at all, and the dashboard
shows the same bare number either way.

DERIVED, NOT GUESSED, when the provider does not say. A .TW symbol is quoted in
TWD by definition of the market it trades on; that is not an inference about
the instrument, it is what the suffix means. The provider's own answer wins
when there is one -- yfinance's fast_info carries `currency` and costs nothing
extra, so the hot poll path gets it for free.
"""

from decimal import Decimal

from app.models.enums import DataSource
from app.services.market_data.base import Quote, currency_for

# --- what the symbol itself already tells us --------------------------------


def test_a_listed_taiwanese_symbol_is_quoted_in_taiwan_dollars():
    assert currency_for("2330.TW", DataSource.YFINANCE) == "TWD"


def test_an_otc_taiwanese_symbol_too():
    assert currency_for("6488.TWO", DataSource.YFINANCE) == "TWD"


def test_a_lettered_etf_is_not_a_special_case():
    assert currency_for("00632R.TW", DataSource.YFINANCE) == "TWD"


def test_a_bare_ticker_is_a_us_listing_and_quoted_in_dollars():
    assert currency_for("AAPL", DataSource.YFINANCE) == "USD"


def test_a_binance_pair_is_quoted_in_its_quote_asset():
    assert currency_for("BTCUSDT", DataSource.BINANCE) == "USDT"
    assert currency_for("ETHBTC", DataSource.BINANCE) == "BTC"


def test_a_market_this_app_does_not_model_gets_no_invented_currency():
    """A .HK or .T symbol is not something this app can price anyway, and
    claiming a currency for it would be exactly the confident wrong answer the
    whole symbol effort exists to avoid."""
    assert currency_for("0700.HK", DataSource.YFINANCE) is None


def test_an_empty_symbol_is_not_a_currency():
    assert currency_for("", DataSource.YFINANCE) is None


# --- the quote carries it ---------------------------------------------------


def test_a_quote_defaults_to_no_currency_rather_than_a_wrong_one():
    """Rows written before this existed have none, and a default of "USD" or
    "TWD" would relabel every one of them."""
    quote = Quote(symbol="AAPL", data_source=DataSource.YFINANCE, price=Decimal(1))

    assert quote.currency is None


def test_the_yfinance_provider_reports_the_currency_it_was_given(monkeypatch):
    """fast_info already carries it, so the five-second poll pays nothing extra
    -- which is the only reason this can live in the hot path at all."""
    from app.services.market_data.providers import yfinance_provider

    class _FastInfo(dict):
        currency = "TWD"

    fast = _FastInfo({"lastPrice": 2375.0, "previousClose": 2350.0})

    class _Ticker:
        def __init__(self, symbol):
            self.fast_info = fast

    monkeypatch.setattr(yfinance_provider.yf, "Ticker", _Ticker)

    quotes = yfinance_provider.YFinanceProvider().get_quotes(["2330.TW"])

    assert quotes["2330.TW"].currency == "TWD"


def test_the_provider_falls_back_to_the_symbol_when_it_says_nothing(monkeypatch):
    """An older yfinance, or a symbol whose fast_info is missing the field.
    Losing the currency entirely would be worse than reading it off a suffix
    that defines it."""
    from app.services.market_data.providers import yfinance_provider

    class _Ticker:
        def __init__(self, symbol):
            self.fast_info = {"lastPrice": 2375.0}

    monkeypatch.setattr(yfinance_provider.yf, "Ticker", _Ticker)

    quotes = yfinance_provider.YFinanceProvider().get_quotes(["2330.TW"])

    assert quotes["2330.TW"].currency == "TWD"


# --- it survives to the screen ----------------------------------------------


def test_the_stored_quote_keeps_the_currency(db_session):
    from app.models.market import MarketQuote
    from app.services.market_data.service import MarketDataService

    service = MarketDataService(providers={})
    service.upsert_quotes(
        db_session,
        {
            "2330.TW": Quote(
                symbol="2330.TW",
                data_source=DataSource.YFINANCE,
                price=Decimal(2375),
                currency="TWD",
            )
        },
    )

    assert db_session.get(MarketQuote, "2330.TW").currency == "TWD"


def test_the_api_hands_the_currency_to_the_page(auth_client):
    """A bare number on the dashboard is the whole problem. The screen cannot
    label it without being told.

    The endpoint fetches live rather than reading the stored row, so the
    provider is stubbed -- what is under test is that the field survives from
    the provider to the JSON, not how it got into the database."""
    from app.main import app
    from app.services.market_data.service import MarketDataService, get_market_data_service

    class _Stub:
        data_source = DataSource.YFINANCE

        def get_quotes(self, symbols):
            return {
                s: Quote(
                    symbol=s,
                    data_source=DataSource.YFINANCE,
                    price=Decimal(2375),
                    currency="TWD",
                )
                for s in symbols
            }

    service = MarketDataService(providers={DataSource.YFINANCE: _Stub()})
    app.dependency_overrides[get_market_data_service] = lambda: service
    try:
        body = auth_client.get("/api/market/quote?symbols=2330.TW").json()
    finally:
        app.dependency_overrides.pop(get_market_data_service, None)

    assert body[0]["currency"] == "TWD"
