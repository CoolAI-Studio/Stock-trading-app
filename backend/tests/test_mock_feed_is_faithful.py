"""The fake feed has to refuse what a real feed would refuse.

MockProvider invented a price for ANY string it was handed -- `setdefault(
symbol, 100.0)`. tests/test_market_data.py says so out loud in a comment
(「MockProvider can't stand in here because it invents a price for every symbol
it's asked about」) and works around it with a hand-rolled stub.

That is not a cosmetic gap. It is the reason a whole family of symbol bugs
shipped: every test in this suite ran against a feed for which 「台積電」 and
「2330」 were perfectly good symbols, so a watchlist row storing a Chinese
company name priced happily in CI and silently never priced in production.
A test double that succeeds where the real thing fails cannot fail a test --
it can only hide one.

WHAT IT REFUSES, AND WHY THOSE:
  Exactly the shapes the app itself declares unpriceable (symbol_search.
  looks_unpriceable) plus one more the bundled registry can settle: a .TW/.TWO
  symbol whose code is not on either board. Everything else is priced, because
  the mock is not a whitelist any more than looks_unpriceable is -- refusing
  every ticker the bundled table has not heard of would make the fake feed
  stricter than the real one, which is its own kind of lie.

  An explicit base_prices entry always wins. A test that genuinely needs a
  made-up symbol can still have one; it just has to say so in the setup rather
  than getting it by accident.
"""

from decimal import Decimal

from app.models.enums import DataSource
from app.services.market_data.base import Timeframe
from app.services.market_data.providers.mock_provider import MockProvider

# --- the two shapes the owner actually types --------------------------------


def test_a_chinese_company_name_gets_no_price():
    """The bug this file exists for. 「台積電」 in a watchlist row priced in every
    test and priced in production never."""
    assert MockProvider().get_quotes(["台積電"]) == {}


def test_a_bare_taiwanese_code_gets_no_price():
    """The dangerous one: on Yahoo a bare 2330 resolves to an unrelated
    Japanese company, so it does not fail -- it succeeds on the wrong stock."""
    assert MockProvider().get_quotes(["2330"]) == {}


def test_a_blank_symbol_gets_no_price():
    assert MockProvider().get_quotes([""]) == {}


def test_a_taiwanese_code_on_neither_board_gets_no_price():
    """9999.TW is the right SHAPE and still not a thing. The bundled listing
    table can settle this one, and a real feed would return nothing for it."""
    assert MockProvider().get_quotes(["9999.TW"]) == {}


# --- what it must still price -----------------------------------------------


def test_real_symbols_are_priced():
    quotes = MockProvider().get_quotes(["2330.TW", "6488.TWO", "00632R.TW", "AAPL"])

    assert set(quotes) == {"2330.TW", "6488.TWO", "00632R.TW", "AAPL"}
    assert all(q.price > 0 for q in quotes.values())


def test_a_us_ticker_the_bundled_table_never_heard_of_is_still_priced():
    """The bundled US alias file holds 53 tickers. Refusing the other several
    thousand would make the fake feed stricter than the real one, which hides
    bugs in the opposite direction."""
    assert "ZZZZ" in MockProvider().get_quotes(["ZZZZ"])


def test_a_crypto_pair_is_priced():
    provider = MockProvider(data_source=DataSource.BINANCE)

    assert "BTCUSDT" in provider.get_quotes(["BTCUSDT"])


def test_a_configured_symbol_is_priced_however_odd_it_looks():
    """The escape hatch, and it is deliberately noisy: the symbol has to be
    written into the test's own setup."""
    provider = MockProvider(base_prices={"台積電": 950.0})

    # Near, not equal: the walk moves every price by up to 0.2% on each tick,
    # and pinning the exact number would be testing the random number
    # generator rather than the escape hatch.
    assert Decimal(940) < provider.get_quotes(["台積電"])["台積電"].price < Decimal(960)


def test_one_refused_symbol_does_not_take_the_others_with_it():
    quotes = MockProvider().get_quotes(["2330.TW", "台積電", "AAPL"])

    assert set(quotes) == {"2330.TW", "AAPL"}


# --- candles refuse on the same terms ---------------------------------------


def test_candles_are_refused_for_a_symbol_that_has_no_quotes():
    """Otherwise a strategy in a test warms up on 300 candles of a symbol that
    can never produce a single quote -- which looks like a working strategy."""
    assert MockProvider().get_bars("台積電", Timeframe.DAY_1, limit=10) == []


def test_candles_still_come_back_for_a_real_symbol():
    assert len(MockProvider().get_bars("2330.TW", Timeframe.DAY_1, limit=10)) == 10


# --- the fake feed labels its prices like the real ones do ------------------


def test_a_mock_quote_carries_a_currency():
    """The real providers gained this; a mock that does not means every
    end-to-end test runs against an unlabelled price, which is the exact
    ambiguity 2330.TW-vs-TSM was fixed to remove."""
    quotes = MockProvider().get_quotes(["2330.TW", "AAPL"])

    assert quotes["2330.TW"].currency == "TWD"
    assert quotes["AAPL"].currency == "USD"


def test_a_binance_pair_is_labelled_with_its_quote_asset():
    provider = MockProvider(data_source=DataSource.BINANCE)

    assert provider.get_quotes(["BTCUSDT"])["BTCUSDT"].currency == "USDT"


# --- the service sees it as an ordinary partial fetch -----------------------


def test_the_service_logs_the_symbol_it_could_not_price(caplog):
    """service.get_quotes already names symbols a provider omitted. The mock
    now goes down that path too, so a test that types a bad symbol gets a
    warning naming it instead of a plausible number."""
    import logging

    from app.services.market_data.service import MarketDataService

    service = MarketDataService(providers={DataSource.YFINANCE: MockProvider()})

    with caplog.at_level(logging.WARNING):
        service.get_quotes(["2330.TW", "台積電"], DataSource.YFINANCE)

    assert any("台積電" in record.message for record in caplog.records)


# --- the timestamp each source can honestly produce -------------------------


def test_a_yfinance_shaped_mock_leaves_the_quote_time_empty():
    """The real yfinance provider returns None -- fast_info has no temporal
    field. A mock that filled one in would make this the one path no
    end-to-end test ever walks."""
    assert MockProvider().get_quotes(["2330.TW"])["2330.TW"].quote_time is None


def test_a_binance_shaped_mock_carries_one():
    """Binance's ticker response really does carry closeTime."""
    provider = MockProvider(data_source=DataSource.BINANCE)

    assert provider.get_quotes(["BTCUSDT"])["BTCUSDT"].quote_time is not None


def test_a_company_name_is_not_labelled_as_a_us_listing():
    """currency_for reads 「no dot, no dash」 as a bare US ticker. 「台積電」 has
    neither and is not a ticker at all -- calling it USD is the confident wrong
    answer the function's own docstring refuses to give."""
    from app.services.market_data.base import currency_for

    assert currency_for("台積電", DataSource.YFINANCE) is None
