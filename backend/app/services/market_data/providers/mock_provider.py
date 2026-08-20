import random
from datetime import UTC, datetime
from decimal import Decimal

from app.models.enums import DataSource
from app.services import symbol_search
from app.services.market_data.base import Bar, Quote, Timeframe, bars_from_closes, currency_for


class MockProvider:
    """Deterministic-shape random-walk provider. Required (not optional) so
    tests -- and local dev without network access -- never touch yfinance or
    Binance.

    It refuses symbols a real feed would have nothing for. That is not
    pedantry: `setdefault(symbol, 100.0)` used to invent a price for any string
    at all, so every test in the suite ran against a feed where 「台積電」 and a
    bare 「2330」 were perfectly good symbols. A double that succeeds where the
    real thing fails cannot fail a test, it can only hide one -- and it did,
    for the entire family of symbol bugs fixed in this repo's recent history.

    What it refuses is exactly what the app itself calls unpriceable, plus a
    .TW/.TWO code that is on neither board. Not a whitelist: an unfamiliar US
    ticker still prices, because a fake feed stricter than the real one hides
    bugs in the other direction.
    """

    def __init__(
        self,
        base_prices: dict[str, float] | None = None,
        data_source: DataSource = DataSource.YFINANCE,
    ) -> None:
        self._prices = dict(base_prices or {})
        # Kept apart from _prices, which the walk writes into on every tick.
        # The escape hatch has to mean 「the test asked for this one by name」,
        # and it would stop meaning that the moment a symbol could get in here
        # merely by having been quoted once.
        self._configured = frozenset(self._prices)
        self.data_source = data_source

    def _can_price(self, symbol: str) -> bool:
        if symbol in self._configured:
            # Said out loud in the test's own setup. A test that genuinely
            # needs an odd symbol may have one; it just cannot get one by
            # accident.
            return True
        if symbol_search.looks_unpriceable(symbol) is not None:
            return False
        return not (
            symbol_search.is_taiwanese(symbol) and symbol_search.listing_for(symbol) is None
        )

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        # Binance's ticker response carries closeTime, so a live-ish 「now」 is
        # what a caller would really get. yfinance's fast_info carries no
        # temporal field at all and the real provider therefore returns None --
        # a mock that filled it in would be the only place that path is never
        # exercised. See tests/test_quote_freshness.py.
        now = datetime.now(UTC) if self.data_source is DataSource.BINANCE else None
        quotes: dict[str, Quote] = {}
        for symbol in symbols:
            if not self._can_price(symbol):
                # Omitted, not raised. Providers drop what they cannot resolve
                # and MarketDataService already names the gap in a warning;
                # going down that same path is the point.
                continue
            price = self._prices.setdefault(symbol, 100.0)
            price = max(0.01, price * (1 + random.uniform(-0.002, 0.002)))
            self._prices[symbol] = price
            quotes[symbol] = Quote(
                symbol=symbol,
                data_source=self.data_source,
                price=Decimal(str(round(price, 4))),
                quote_time=now,
                currency=currency_for(symbol, self.data_source),
            )
        return quotes

    def get_bars(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Bar]:
        """Unlike the quote walk above this is deterministic: an indicator
        replayed over it gives the same answer twice, which is what makes it
        usable in a test.

        Refuses on the same terms as get_quotes. A symbol with 300 candles and
        no quote is a strategy that warms up, evaluates, and can never fire --
        which reads as a working strategy right up until it matters.
        """
        if not self._can_price(symbol):
            return []
        base = self._prices.setdefault(symbol, 100.0)
        closes = [round(base + (i % 7) - 3, 4) for i in range(limit)]
        return bars_from_closes(symbol, timeframe, closes)
