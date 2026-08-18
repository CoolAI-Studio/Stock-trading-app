import random
from datetime import UTC, datetime
from decimal import Decimal

from app.models.enums import DataSource
from app.services.market_data.base import Bar, Quote, Timeframe, bars_from_closes


class MockProvider:
    """Deterministic-shape random-walk provider. Required (not optional) so
    tests -- and local dev without network access -- never touch yfinance or
    Binance."""

    def __init__(
        self,
        base_prices: dict[str, float] | None = None,
        data_source: DataSource = DataSource.YFINANCE,
    ) -> None:
        self._prices = dict(base_prices or {})
        self.data_source = data_source

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        now = datetime.now(UTC)
        quotes: dict[str, Quote] = {}
        for symbol in symbols:
            price = self._prices.setdefault(symbol, 100.0)
            price = max(0.01, price * (1 + random.uniform(-0.002, 0.002)))
            self._prices[symbol] = price
            quotes[symbol] = Quote(
                symbol=symbol,
                data_source=self.data_source,
                price=Decimal(str(round(price, 4))),
                quote_time=now,
            )
        return quotes

    def get_bars(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Bar]:
        """Unlike the quote walk above this is deterministic: an indicator
        replayed over it gives the same answer twice, which is what makes it
        usable in a test."""
        base = self._prices.setdefault(symbol, 100.0)
        closes = [round(base + (i % 7) - 3, 4) for i in range(limit)]
        return bars_from_closes(symbol, timeframe, closes)
