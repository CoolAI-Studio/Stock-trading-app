"""When a price was actually last traded, rather than when we asked for it.

`quote_time` exists to answer 「這個價格是什麼時候的」. schemas/position.py says so
in its own comment -- 「quote_time comes along so a price left over from Friday」
-- and positions.py falls back to `fetched_at` only when it is absent, which is
the shape of a field that is supposed to carry the exchange's answer.

Both providers filled it with `datetime.now(UTC)`, taken once at the top of the
loop. So it always said 「just now」, for every symbol, including ones that had
not traded in years. A delisted stock keeps returning its final close forever,
and that close was persisted with a timestamp claiming it was current. The one
field built to reveal staleness was the field guaranteeing it could never be
seen.

WHAT EACH PROVIDER CAN HONESTLY SAY:
  Binance's ticker response carries `closeTime`, a real millisecond epoch. Use it.
  yfinance's fast_info carries no timestamp at all -- currency, day_high,
  last_price and so on, but nothing temporal. So the honest answer there is
  None, and `fetched_at` (which has always recorded when WE asked) carries the
  display. None is not a regression: it replaces a confident wrong answer with
  an absent one, and the fallback for that was already written.
"""

from datetime import UTC, datetime
from decimal import Decimal

# --- yfinance cannot know, and must not pretend -----------------------------


def test_the_yfinance_provider_does_not_invent_a_quote_time(monkeypatch):
    """fast_info has no temporal field. Claiming 「now」 made a 2016 close look
    like a live price."""
    from app.services.market_data.providers import yfinance_provider

    class _Ticker:
        def __init__(self, symbol):
            self.fast_info = {"lastPrice": 2375.0, "previousClose": 2350.0}

    monkeypatch.setattr(yfinance_provider.yf, "Ticker", _Ticker)

    quote = yfinance_provider.YFinanceProvider().get_quotes(["2330.TW"])["2330.TW"]

    assert quote.quote_time is None


def test_fast_info_really_has_no_timestamp_field():
    """Pinned so that if yfinance ever adds one, this fails and somebody wires
    it up instead of leaving the honest-but-empty answer forever."""
    import yfinance as yf

    fields = {f for f in dir(yf.Ticker("AAPL").fast_info) if not f.startswith("_")}

    assert not fields & {"regularMarketTime", "last_trade_time", "quote_time", "timestamp"}


# --- binance can, and does --------------------------------------------------


def test_the_binance_provider_uses_the_exchanges_own_close_time(monkeypatch):
    from app.services.market_data.providers import binance_provider

    closed_at = datetime(2026, 8, 20, 4, 30, tzinfo=UTC)

    class _Response:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {
                "lastPrice": "65000.00",
                "prevClosePrice": "64000.00",
                "priceChangePercent": "1.5",
                "volume": "1000",
                "closeTime": int(closed_at.timestamp() * 1000),
            }

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def get(self, *_a, **_kw):
            return _Response()

    monkeypatch.setattr(binance_provider.httpx, "Client", lambda **_kw: _Client())

    quote = binance_provider.BinanceProvider().get_quotes(["BTCUSDT"])["BTCUSDT"]

    assert quote.quote_time == closed_at


def test_a_binance_response_without_a_close_time_says_nothing(monkeypatch):
    """Better an absent answer than our own clock wearing the exchange's
    label."""
    from app.services.market_data.providers import binance_provider

    class _Response:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"lastPrice": "65000.00"}

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def get(self, *_a, **_kw):
            return _Response()

    monkeypatch.setattr(binance_provider.httpx, "Client", lambda **_kw: _Client())

    quote = binance_provider.BinanceProvider().get_quotes(["BTCUSDT"])["BTCUSDT"]

    assert quote.quote_time is None


def test_a_nonsense_close_time_is_ignored_rather_than_crashing(monkeypatch):
    """One malformed field must not lose the price along with it -- the poll
    carries on for every other symbol either way."""
    from app.services.market_data.providers import binance_provider

    class _Response:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"lastPrice": "65000.00", "closeTime": "not-a-number"}

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def get(self, *_a, **_kw):
            return _Response()

    monkeypatch.setattr(binance_provider.httpx, "Client", lambda **_kw: _Client())

    quotes = binance_provider.BinanceProvider().get_quotes(["BTCUSDT"])

    assert quotes["BTCUSDT"].price == Decimal("65000.00")
    assert quotes["BTCUSDT"].quote_time is None


# --- the display still has something to show --------------------------------


def test_a_position_falls_back_to_when_we_fetched(auth_client, db_session):
    """positions.py already reads `quote.quote_time or quote.fetched_at`. That
    fallback was dead code while the providers always supplied a value; it is
    now the normal path for yfinance, and it has to actually work."""
    from app.models.enums import DataSource
    from app.models.market import MarketQuote
    from app.models.mixins import utcnow
    from app.models.position import Position
    from app.models.user import User

    user_id = db_session.query(User).first().id
    fetched = utcnow()
    db_session.add(Position(user_id=user_id, symbol="2330.TW", quantity=Decimal(1000)))
    db_session.add(
        MarketQuote(
            symbol="2330.TW",
            data_source=DataSource.YFINANCE,
            price=Decimal(950),
            quote_time=None,
            fetched_at=fetched,
        )
    )
    db_session.commit()

    body = auth_client.get("/api/positions").json()

    assert body[0]["quote_time"] is not None, "the page would otherwise show nothing at all"
