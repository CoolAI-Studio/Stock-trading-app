"""A symbol and its data source are two halves of one answer.

Nothing ever checked that they matched, and the mismatch is silent in both
directions:

  data_source=binance with 2330.TW -- Binance does not list Taiwanese equities,
  so the symbol never prices at all. Worse, market_calendar returns 「cannot
  tell」 for anything on Binance (crypto trades continuously), so the app also
  believes this symbol's market is open at 3am and polls it every five seconds
  all night.

  data_source=yfinance with BTC-USD -- which is yfinance's OWN crypto ticker
  format, and a perfectly real, priceable, 24-hour instrument. market_calendar
  sees a bare ticker with no dot and classifies it as a US equity, so it is
  「closed」 from 16:00 to 09:30 New York. Crypto's largest moves happen in
  exactly those hours. A stop-loss on BTC-USD was never checked overnight and
  the owner had no way to know.

TWO PLACES, BECAUSE THERE ARE TWO POPULATIONS. New rows are refused at the
input, where the person is present and can fix it. Rows that already exist
cannot be refused retroactively, so the calendar gets the same defence -- which
is the precedent already set for a bare Taiwanese code that lost its suffix.

REFUSED ONLY WHEN CERTAIN, which is this area's standing rule. Binance holding
a .TW listing is certain. A seven-character symbol ending in USDT being a US
ticker is certain (they are at most five letters). Everything else is left
alone: refusing a symbol the owner cannot then add is a worse failure than
polling one that turns out not to price.
"""

import pytest

from app.enums import DataSource
from app.services import market_calendar, symbol_search

# --- refused where somebody is present to fix it ----------------------------


def test_a_taiwanese_symbol_cannot_come_from_binance():
    problem = symbol_search.market_mismatch("2330.TW", DataSource.BINANCE)

    assert problem and "Binance" in problem


def test_the_refusal_says_which_source_to_pick_instead():
    """A refusal that does not name the fix just moves the guessing."""
    problem = symbol_search.market_mismatch("2330.TW", DataSource.BINANCE)

    assert "yfinance" in problem.lower()


def test_a_binance_pair_cannot_come_from_yfinance():
    problem = symbol_search.market_mismatch("BTCUSDT", DataSource.YFINANCE)

    assert problem and "Binance" in problem


def test_a_real_pairing_is_left_alone():
    assert symbol_search.market_mismatch("BTCUSDT", DataSource.BINANCE) is None
    assert symbol_search.market_mismatch("2330.TW", DataSource.YFINANCE) is None
    assert symbol_search.market_mismatch("AAPL", DataSource.YFINANCE) is None


def test_yfinances_own_crypto_ticker_is_left_alone():
    """BTC-USD is how yfinance names bitcoin. It is a real instrument from a
    real source and must not be refused."""
    assert symbol_search.market_mismatch("BTC-USD", DataSource.YFINANCE) is None


def test_a_short_ticker_that_merely_ends_in_a_quote_asset_is_not_refused():
    """US tickers run to five letters. Refusing one because its last four
    happen to spell a stablecoin would make a legitimate stock unaddable, and
    that is the worse failure of the two."""
    assert symbol_search.market_mismatch("USDT", DataSource.YFINANCE) is None


def test_the_strategy_form_refuses_the_mismatch(auth_client):
    from app.schemas.strategy import StrategyCreate

    with pytest.raises(ValueError):
        StrategyCreate(
            name="x",
            symbol="2330.TW",
            data_source=DataSource.BINANCE,
            source_code="def on_tick(ctx):\n    pass\n",
        )


def test_the_watchlist_refuses_it_too(auth_client):
    resp = auth_client.post("/api/watchlist", json={"symbol": "2330.TW", "data_source": "binance"})

    assert resp.status_code == 422, resp.text


# --- and the rows that already exist ----------------------------------------


@pytest.mark.real_market_hours
def test_a_taiwanese_symbol_on_binance_still_keeps_taiwanese_hours():
    """It cannot be refused retroactively, and 「always open」 means polling a
    shut market every five seconds all night against a source that cannot
    price it anyway."""
    from datetime import UTC, datetime

    three_am_taipei = datetime(2026, 8, 19, 19, 0, tzinfo=UTC)

    assert not market_calendar.is_open("2330.TW", three_am_taipei, DataSource.BINANCE)


@pytest.mark.real_market_hours
def test_yfinances_crypto_ticker_is_open_at_three_in_the_morning():
    """The mirror bug, and the more damaging one: a bare ticker with no dot was
    read as a US equity, so a stop-loss on BTC-USD went unchecked from 16:00 to
    09:30 New York -- the hours crypto actually moves."""
    from datetime import UTC, datetime

    three_am_new_york = datetime(2026, 8, 19, 7, 0, tzinfo=UTC)

    assert market_calendar.is_open("BTC-USD", three_am_new_york, DataSource.YFINANCE)


@pytest.mark.real_market_hours
def test_an_ordinary_us_ticker_still_closes_at_night():
    """The dash is what says crypto. Widening this to every bare ticker would
    put every US stock back to being polled around the clock."""
    from datetime import UTC, datetime

    three_am_new_york = datetime(2026, 8, 19, 7, 0, tzinfo=UTC)

    assert not market_calendar.is_open("AAPL", three_am_new_york, DataSource.YFINANCE)


@pytest.mark.real_market_hours
def test_a_real_binance_pair_is_still_always_open():
    from datetime import UTC, datetime

    assert market_calendar.is_open(
        "BTCUSDT", datetime(2026, 8, 19, 19, 0, tzinfo=UTC), DataSource.BINANCE
    )


# --- the half-edit, which the schema cannot see -----------------------------
#
# PATCH /strategies/{id} with only one of the two fields cannot be judged by
# the request body: the other half lives on the stored row. The router
# re-checks the merged result, which is the only place both halves exist.


SOURCE = """
class Strategy:
    def __init__(self):
        self.name = "hold"
        self.symbol = "2330.TW"

    def on_tick(self, current_price: float) -> str:
        return "HOLD"
"""


def _strategy(auth_client, **kw) -> int:
    body = {
        "name": "s",
        "symbol": "2330.TW",
        "data_source": "yfinance",
        "source_code": SOURCE,
    }
    body.update(kw)
    resp = auth_client.post("/api/strategies", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_switching_only_the_source_is_refused(auth_client):
    strategy_id = _strategy(auth_client)

    resp = auth_client.patch(f"/api/strategies/{strategy_id}", json={"data_source": "binance"})

    assert resp.status_code == 422, resp.text


def test_switching_only_the_symbol_is_refused(auth_client):
    strategy_id = _strategy(auth_client, symbol="BTCUSDT", data_source="binance")

    resp = auth_client.patch(f"/api/strategies/{strategy_id}", json={"symbol": "2330.TW"})

    assert resp.status_code == 422, resp.text


def test_the_refused_edit_did_not_half_happen(auth_client):
    """The router sets the fields before it can check the merged pair, so the
    refusal has to undo them. A rejected request that still moved the row is
    worse than either outcome on its own."""
    strategy_id = _strategy(auth_client)

    auth_client.patch(f"/api/strategies/{strategy_id}", json={"data_source": "binance"})

    after = auth_client.get(f"/api/strategies/{strategy_id}").json()
    assert after["data_source"] == "yfinance"


def test_an_ordinary_edit_still_goes_through(auth_client):
    strategy_id = _strategy(auth_client)

    resp = auth_client.patch(f"/api/strategies/{strategy_id}", json={"name": "renamed"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "renamed"
