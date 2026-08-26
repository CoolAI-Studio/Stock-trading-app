import logging
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx

from app.enums import DataSource
from app.services.market_data.base import Bar, BarFetchError, Quote, Timeframe, currency_for

_TICKER_URL = "https://api.binance.com/api/v3/ticker/24hr"
logger = logging.getLogger("app.market_data.binance")

_KLINES_URL = "https://api.binance.com/api/v3/klines"

# Binance spells three of the intervals differently from Yahoo. Timeframe
# uses Yahoo's spelling (see base.py), so the crypto side translates here
# rather than making every strategy know which provider it is talking to.
_BINANCE_INTERVAL: dict[Timeframe, str] = {
    Timeframe.MINUTE_1: "1m",
    Timeframe.MINUTE_5: "5m",
    Timeframe.MINUTE_15: "15m",
    Timeframe.MINUTE_30: "30m",
    Timeframe.HOUR_1: "1h",
    Timeframe.HOUR_4: "4h",
    # The one Yahoo will not serve at all, and the reason SUPPORTED_TIMEFRAMES
    # is keyed by source rather than being one global list.
    Timeframe.HOUR_12: "12h",
    Timeframe.DAY_1: "1d",
    Timeframe.WEEK_1: "1w",
    Timeframe.MONTH_1: "1M",
}


class BinanceProvider:
    data_source = DataSource.BINANCE

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
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

                # The exchange's own answer to "when was this". Unlike
                # yfinance's fast_info, Binance supplies one -- so this is the
                # provider that can actually populate the staleness field
                # rather than stamping our clock on it.
                closed_at = _close_time(data.get("closeTime"))

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
                    quote_time=closed_at,
                    # The quote asset is literally the tail of the pair's own
                    # name, so this is reading the symbol rather than guessing.
                    currency=currency_for(symbol, self.data_source),
                )
        return quotes

    def get_bars(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Bar]:
        try:
            with httpx.Client(timeout=10.0) as http_client:
                response = http_client.get(
                    _KLINES_URL,
                    params={
                        "symbol": symbol,
                        "interval": _BINANCE_INTERVAL[timeframe],
                        "limit": limit,
                    },
                )
                response.raise_for_status()
                rows = response.json()
        except Exception as exc:
            # Everything, not just HTTPError/ValueError: anything that escapes
            # here reaches the chart endpoint as a 500 instead of a sentence
            # the page can render, and reaches the market loop as a crashed
            # poll. One unreachable pair must take neither down.
            #
            # A FAILURE, not an absence -- the same disease the yfinance side
            # had. Bars get cached, so returning [] here would store 「this pair
            # has no history」 as fact for the whole TTL after one dropped
            # connection. The caller decides how long to wait before asking
            # again; it cannot decide anything if both answers look alike.
            logger.warning("binance bars failed for %s", symbol, exc_info=True)
            raise BarFetchError(f"{symbol}: {type(exc).__name__}") from exc

        bars: list[Bar] = []
        for row in rows:
            try:
                # [openTime, open, high, low, close, volume, closeTime, ...]
                bars.append(
                    Bar(
                        symbol=symbol,
                        timeframe=timeframe,
                        timestamp=datetime.fromtimestamp(row[0] / 1000, UTC),
                        open=float(row[1]),
                        high=float(row[2]),
                        low=float(row[3]),
                        close=float(row[4]),
                        volume=float(row[5]),
                    )
                )
            except (IndexError, TypeError, ValueError):
                continue
        return bars

    @staticmethod
    def _safe_decimal(value) -> Decimal | None:
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None


def _close_time(raw: object) -> datetime | None:
    """Binance's millisecond epoch, or None.

    None rather than our own clock when it is missing or malformed: an absent
    answer is honest, and a wrong one hides exactly what this field exists to
    show. One bad field must not cost the price alongside it.
    """
    try:
        return datetime.fromtimestamp(int(raw) / 1000, tz=UTC)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError, OSError):
        return None
