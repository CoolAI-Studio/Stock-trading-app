from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx

from app.models.enums import DataSource
from app.services.market_data.base import Quote

_TICKER_URL = "https://api.binance.com/api/v3/ticker/24hr"


class BinanceProvider:
    data_source = DataSource.BINANCE

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        now = datetime.now(UTC)
        quotes: dict[str, Quote] = {}
        with httpx.Client(timeout=10.0) as http_client:
            for symbol in symbols:
                try:
                    response = http_client.get(_TICKER_URL, params={"symbol": symbol})
                    response.raise_for_status()
                    data = response.json()
                    price_dec = Decimal(str(data["lastPrice"]))
                except (httpx.HTTPError, KeyError, InvalidOperation, ValueError):
                    continue

                prev_close_dec = self._safe_decimal(data.get("prevClosePrice"))
                change_pct = self._safe_decimal(data.get("priceChangePercent"))
                volume_dec = self._safe_decimal(data.get("volume"))

                quotes[symbol] = Quote(
                    symbol=symbol,
                    data_source=self.data_source,
                    price=price_dec,
                    prev_close=prev_close_dec,
                    change_pct=change_pct,
                    volume=volume_dec,
                    quote_time=now,
                )
        return quotes

    @staticmethod
    def _safe_decimal(value) -> Decimal | None:
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
