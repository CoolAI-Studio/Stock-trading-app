from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import yfinance as yf

from app.models.enums import DataSource
from app.services.market_data.base import Quote


class YFinanceProvider:
    data_source = DataSource.YFINANCE

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        if not symbols:
            return {}

        now = datetime.now(UTC)
        quotes: dict[str, Quote] = {}
        for symbol in symbols:
            try:
                fast_info = yf.Ticker(symbol).fast_info
                price = fast_info["lastPrice"]
                prev_close = fast_info.get("previousClose") if hasattr(fast_info, "get") else None
            except Exception:
                continue

            if price is None:
                continue

            try:
                # yfinance returns raw floats with binary-precision noise
                # (e.g. 305.92999267578125) -- round before persisting/
                # displaying rather than storing that noise verbatim.
                price_dec = Decimal(str(round(price, 4)))
                prev_close_dec = Decimal(str(round(prev_close, 4))) if prev_close else None
            except InvalidOperation:
                continue

            change_pct = None
            if prev_close_dec:
                change_pct = round(((price_dec - prev_close_dec) / prev_close_dec) * 100, 4)

            quotes[symbol] = Quote(
                symbol=symbol,
                data_source=self.data_source,
                price=price_dec,
                prev_close=prev_close_dec,
                change_pct=change_pct,
                quote_time=now,
            )
        return quotes
