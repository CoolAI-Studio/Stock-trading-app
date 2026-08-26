"""A price the feed stopped supplying must stop looking like a live one.

The quote cache merged every fetch into one bucket per source and never
removed anything: `cached_quotes = {**cached_quotes, **fresh}`. A symbol the
provider stopped answering for kept its last entry, and a full refresh could
not dislodge it -- the refresh asks for every symbol, the provider omits the
dead one, and the merge preserves it.

So a delisted ticker, a symbol that started 404ing, a provider that quietly
began rejecting one name: all of them went on serving the same number, for as
long as the process lived. And `upsert_quotes` stamped `fetched_at = utcnow()`
on every single poll, so the row said the frozen price had arrived seconds ago.
Stop-loss, take-profit and every threshold strategy compared against a number
that could no longer move, behind a timestamp that said it just had.

TWO THINGS ARE TRUE AT ONCE, which is why this is not simply 「evict on the
first miss」:
  A one-poll hiccup must not open a gap. No quote means no evaluation, and for
  an alerting product a skipped evaluation is a missed alert.
  An indefinite failure must not be served as live. Serving is a bridge over a
  hiccup, not a substitute for a feed.

So a cached quote may outlive a failed refresh, and only for a bounded time,
after which the symbol is genuinely missing -- which is a state the rest of the
system already knows how to see (service.get_quotes logs it by name).

And the timestamp tells the truth throughout: `fetched_at` records when the
PROVIDER answered, not when somebody last asked us.
"""

import logging
from decimal import Decimal

from app.enums import DataSource
from app.services.market_data.base import Quote
from app.services.market_data.service import MarketDataService


class _Feed:
    """Answers for whatever is in `alive`, omits the rest -- which is how a
    real provider reports a symbol it cannot resolve."""

    data_source = DataSource.YFINANCE

    def __init__(self, alive: set[str], price: int = 100) -> None:
        self.alive = set(alive)
        self.price = price

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        return {
            s: Quote(symbol=s, data_source=DataSource.YFINANCE, price=Decimal(self.price))
            for s in symbols
            if s in self.alive
        }

    def get_bars(self, symbol, timeframe, limit):
        return []


def _service(feed: _Feed, clock: list[float]) -> MarketDataService:
    return MarketDataService(providers={DataSource.YFINANCE: feed}, clock=lambda: clock[0])


def _price(service, symbol) -> Decimal | None:
    quotes = service.get_quotes([symbol], DataSource.YFINANCE)
    return quotes[symbol].price if symbol in quotes else None


# --- a hiccup must not open a gap -------------------------------------------


def test_one_failed_refresh_still_serves_the_last_price():
    """No quote means no evaluation, and a skipped evaluation is a missed
    alert. Bridging a brief outage is the cache earning its keep."""
    clock = [0.0]
    feed = _Feed({"2330.TW"})
    service = _service(feed, clock)
    assert _price(service, "2330.TW") == Decimal(100)

    feed.alive.clear()
    clock[0] = 20.0  # past the 15s yfinance TTL, so a full refresh is due

    assert _price(service, "2330.TW") == Decimal(100)


# --- but it must not become the feed ----------------------------------------


def test_a_price_nobody_will_confirm_is_eventually_withdrawn():
    """The bug: at t=6000 the provider had said nothing for over an hour and
    the service was still handing out the number from t=0."""
    clock = [0.0]
    feed = _Feed({"2330.TW"})
    service = _service(feed, clock)
    _price(service, "2330.TW")

    feed.alive.clear()
    clock[0] = 3600.0

    assert _price(service, "2330.TW") is None


def test_withdrawing_one_symbol_leaves_the_others_alone():
    """They share a cache bucket. One dead name must not take the live ones
    with it -- that would turn a single bad watchlist row into a total
    outage."""
    clock = [0.0]
    feed = _Feed({"2330.TW", "AAPL"})
    service = _service(feed, clock)
    service.get_quotes(["2330.TW", "AAPL"], DataSource.YFINANCE)

    feed.alive = {"AAPL"}
    clock[0] = 3600.0

    quotes = service.get_quotes(["2330.TW", "AAPL"], DataSource.YFINANCE)
    assert set(quotes) == {"AAPL"}


def test_a_symbol_that_comes_back_is_served_again():
    """A provider outage is not a death sentence for the symbol."""
    clock = [0.0]
    feed = _Feed({"2330.TW"})
    service = _service(feed, clock)
    _price(service, "2330.TW")

    feed.alive.clear()
    clock[0] = 3600.0
    assert _price(service, "2330.TW") is None

    feed.alive = {"2330.TW"}
    feed.price = 250
    clock[0] = 3620.0

    assert _price(service, "2330.TW") == Decimal(250)


def test_the_withdrawal_is_announced(caplog):
    """service.get_quotes already names symbols that did not come back. The
    withdrawn one has to go down that path, or it disappears in silence."""
    clock = [0.0]
    feed = _Feed({"2330.TW"})
    service = _service(feed, clock)
    _price(service, "2330.TW")
    feed.alive.clear()
    clock[0] = 3600.0

    with caplog.at_level(logging.WARNING):
        service.get_quotes(["2330.TW"], DataSource.YFINANCE)

    assert any("2330.TW" in record.message for record in caplog.records)


# --- the timestamp says when the FEED answered ------------------------------


def test_a_quote_records_when_the_provider_answered():
    clock = [0.0]
    service = _service(_Feed({"2330.TW"}), clock)

    quote = service.get_quotes(["2330.TW"], DataSource.YFINANCE)["2330.TW"]

    assert quote.fetched_at is not None


def test_a_price_served_from_cache_keeps_its_original_timestamp():
    """This is the half that made the frozen price invisible: every poll
    re-stamped it as 「just now」, so nothing downstream could tell."""
    clock = [0.0]
    feed = _Feed({"2330.TW"})
    service = _service(feed, clock)
    first = service.get_quotes(["2330.TW"], DataSource.YFINANCE)["2330.TW"].fetched_at

    feed.alive.clear()
    clock[0] = 20.0

    assert service.get_quotes(["2330.TW"], DataSource.YFINANCE)["2330.TW"].fetched_at == first


def test_the_stored_row_carries_that_timestamp_rather_than_now(db_session):
    """positions.py falls back to `fetched_at` to show a price's age. It can
    only do that if the column holds when the price arrived."""
    from datetime import UTC, datetime, timedelta

    from app.models.market import MarketQuote

    answered = datetime.now(UTC) - timedelta(hours=3)
    service = MarketDataService(providers={})
    service.upsert_quotes(
        db_session,
        {
            "2330.TW": Quote(
                symbol="2330.TW",
                data_source=DataSource.YFINANCE,
                price=Decimal(950),
                fetched_at=answered,
            )
        },
    )

    row = db_session.get(MarketQuote, "2330.TW")
    assert abs((row.fetched_at.replace(tzinfo=UTC) - answered).total_seconds()) < 2


def test_a_quote_without_one_still_gets_stamped(db_session):
    """Quotes built by hand -- in tests, and anywhere else that does not go
    through the service's fetch path -- must not land with an empty column."""
    from app.models.market import MarketQuote

    service = MarketDataService(providers={})
    service.upsert_quotes(
        db_session,
        {"AAPL": Quote(symbol="AAPL", data_source=DataSource.YFINANCE, price=Decimal(200))},
    )

    assert db_session.get(MarketQuote, "AAPL").fetched_at is not None
