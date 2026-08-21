"""How big each trade is, as a fraction of what the account is worth.

Every backtest bought a FIXED number of units -- `assumptions.quantity`,
default 1 -- and the result reported a return against `initial_capital`. Those
two numbers were never connected to each other, and the note in the result said
so outright: 「起始本金…只用來換算報酬率與最大回撤，不會擋下任何一筆買進」.

So the headline figure measured the wrong thing, in a way that is invisible
because it is a plausible number:

  One unit of a NT$2,375 stock against NT$100,000 of capital is 2.4% invested.
  A strategy that doubles that stock reports a 2.4% return, and reads as a
  strategy barely worth running.
  One unit of a NT$20 stock is 0.02% invested. The SAME strategy on that stock
  reports 0.02%, and the two runs are not comparable with each other even
  though the comparison is the entire reason the owner ran both.

And nothing ever compounds. A fixed unit size means a strategy that has tripled
the account still buys one unit, so ten years of a good strategy plots as a
straight line rather than a curve.

PERCENT-OF-EQUITY is the sizing that makes the number mean 「what this strategy
did to my money」. Size each entry at a fraction of what the account is worth
when it enters, and the equity curve IS the strategy's return.

WHY IT IS ALSO SAFER. Fixed sizing can spend cash the account does not have --
the note above admits it -- because nothing checks affordability. Sizing from
equity cannot: buying `equity * pct / price` units at `price` leaves
`equity * (1 - pct)`, which for pct <= 1 is never negative.

WHAT IT DOES NOT MODEL, and neither did the fixed mode: lot sizes. Taiwanese
round lots are 1,000 shares and this buys fractions. Stated in the result's
notes rather than silently rounded, because rounding down to a whole lot on a
NT$100,000 account would quietly turn most strategies into no-ops.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.services.backtest import BacktestAssumptions, PositionSizing, run_backtest
from app.services.market_data.base import Bar, Timeframe

_START = datetime(2026, 1, 5, tzinfo=UTC)

BUY_THEN_SELL = """
class Strategy:
    def __init__(self):
        self.name = "round_trip"
        self.symbol = "TEST"
        self.timeframe = "1d"
        self.warmup_bars = 0
        self.seen = 0

    def on_bar(self, bar) -> str:
        self.seen += 1
        if self.seen == 1:
            return "BUY"
        if self.seen == 3:
            return "SELL"
        return "HOLD"
"""

COMPOUNDING = """
class Strategy:
    def __init__(self):
        self.name = "in_and_out"
        self.symbol = "TEST"
        self.timeframe = "1d"
        self.warmup_bars = 0
        self.seen = 0

    def on_bar(self, bar) -> str:
        self.seen += 1
        # Two complete round trips: in on bar 1 out on bar 2, in on 3 out on 4.
        if self.seen in (1, 3):
            return "BUY"
        if self.seen in (2, 4):
            return "SELL"
        return "HOLD"
"""


def _bars(closes: list[float], opens: list[float] | None = None) -> list[Bar]:
    """Daily candles. `opens` matters here more than in most of these tests:
    the default fill basis is NEXT_OPEN, so an entry sized 「100% of equity」
    is sized at the price of the FOLLOWING candle's open, not at the close the
    signal was computed from."""
    opens = opens if opens is not None else closes
    return [
        Bar(
            symbol="TEST",
            timeframe=Timeframe.DAY_1,
            timestamp=_START + timedelta(days=i),
            open=opens[i],
            high=max(close, opens[i]) + 1,
            low=min(close, opens[i]) - 1,
            close=close,
            volume=1000.0,
        )
        for i, close in enumerate(closes)
    ]


# BUY on bar 1 fills at bar 2's open; SELL on bar 3 fills at bar 4's open. These
# opens make the entry cost exactly 100 and the exit pay exactly 120, so the
# arithmetic in the assertions below is arithmetic and not a fixture readout.
_IN_AT_100_OUT_AT_120 = ([100, 110, 120, 130], [100, 100, 120, 120])


def _free(**kw) -> BacktestAssumptions:
    """Costs off, so the arithmetic under test is the sizing arithmetic."""
    return BacktestAssumptions(
        commission_rate=Decimal(0),
        slippage_rate=Decimal(0),
        sell_tax_rate=Decimal(0),
        minimum_fee=Decimal(0),
        **kw,
    )


def _percent(pct: str = "1", capital: str = "100000") -> BacktestAssumptions:
    return _free(
        position_sizing=PositionSizing.PERCENT_OF_EQUITY,
        equity_pct=Decimal(pct),
        initial_capital=Decimal(capital),
    )


# --- nothing changes for anyone who does not ask -----------------------------


def test_fixed_quantity_is_still_the_default():
    """Saved runs are compared against each other. A default that changed would
    silently make every historical run incomparable with every new one."""
    assert BacktestAssumptions().position_sizing == PositionSizing.FIXED_QUANTITY


def test_a_fixed_run_trades_the_number_it_was_given():
    result = run_backtest(
        source_code=BUY_THEN_SELL,
        bars=_bars([100, 110, 120, 130]),
        assumptions=_free(quantity=Decimal(7)),
    )

    assert result.trades[0].quantity == Decimal(7)


# --- the size itself ---------------------------------------------------------


def test_a_full_equity_entry_spends_the_whole_account():
    """100,000 at a price of 100 is 1,000 units, and no cash left over."""
    result = run_backtest(
        source_code=BUY_THEN_SELL,
        bars=_bars(*_IN_AT_100_OUT_AT_120),
        assumptions=_percent(),
    )

    assert result.trades[0].quantity == Decimal(1000)


def test_a_half_equity_entry_spends_half():
    result = run_backtest(
        source_code=BUY_THEN_SELL,
        bars=_bars(*_IN_AT_100_OUT_AT_120),
        assumptions=_percent(pct="0.5"),
    )

    assert result.trades[0].quantity == Decimal(500)


def test_an_expensive_stock_and_a_cheap_one_now_report_the_same_return():
    """The whole point. The same strategy, the same price MOVEMENT, two price
    LEVELS -- under fixed sizing these reported 2.4% and 0.02% and were not
    comparable with each other."""
    cheap = run_backtest(
        source_code=BUY_THEN_SELL, bars=_bars([20, 22, 24, 24]), assumptions=_percent()
    )
    dear = run_backtest(
        source_code=BUY_THEN_SELL, bars=_bars([2000, 2200, 2400, 2400]), assumptions=_percent()
    )

    assert cheap.summary.total_return_pct == dear.summary.total_return_pct


def test_the_return_is_the_price_move_when_fully_invested():
    """Bought at 100, sold at 120, everything in: +20%."""
    result = run_backtest(
        source_code=BUY_THEN_SELL, bars=_bars(*_IN_AT_100_OUT_AT_120), assumptions=_percent()
    )

    assert result.summary.total_return_pct == Decimal("20.00")


# --- it compounds ------------------------------------------------------------


def test_the_second_trade_is_sized_off_what_the_first_one_earned():
    """A fixed unit size means a strategy that has tripled the account still
    buys one unit, so a decade of a good strategy plots as a straight line."""
    # In at 100, out at 200 (the account doubles), then in at 100 again -- so
    # the second entry buys twice as many units off twice as much money.
    result = run_backtest(
        source_code=COMPOUNDING,
        bars=_bars([100, 200, 100, 200, 200], [100, 100, 200, 100, 200]),
        assumptions=_percent(),
    )

    assert len(result.trades) == 2, result.trades
    assert result.trades[1].quantity > result.trades[0].quantity


def test_a_shrinking_account_buys_less():
    # In at 100, out at 50 (the account halves), then in at 100 again.
    result = run_backtest(
        source_code=COMPOUNDING,
        bars=_bars([100, 50, 100, 50, 50], [100, 100, 50, 100, 50]),
        assumptions=_percent(),
    )

    assert len(result.trades) == 2, result.trades
    assert result.trades[1].quantity < result.trades[0].quantity


# --- it cannot spend money the account does not have ------------------------


def test_cash_never_goes_negative_the_way_fixed_sizing_can():
    """The fixed mode's own note admits it does not check affordability. Sizing
    from equity removes the question rather than answering it."""
    result = run_backtest(
        source_code=BUY_THEN_SELL,
        bars=_bars([100000, 110000, 120000, 120000]),
        assumptions=_percent(capital="1000"),
    )

    assert all(point.equity >= 0 for point in result.equity_curve)


# --- what it refuses ---------------------------------------------------------


def test_a_zero_fraction_is_not_a_position_size():
    with pytest.raises(ValueError):
        _percent(pct="0")


def test_more_than_the_whole_account_is_refused():
    """There is no margin here. Allowing 150% would report leverage the
    simulation does not model."""
    with pytest.raises(ValueError):
        _percent(pct="1.5")


def test_a_negative_fraction_is_refused():
    with pytest.raises(ValueError):
        _percent(pct="-0.5")


def test_the_fixed_quantity_is_not_validated_in_percent_mode():
    """It is unused there, and demanding a meaningful value for a field the run
    ignores is how a form grows a question nobody can answer."""
    _free(
        position_sizing=PositionSizing.PERCENT_OF_EQUITY,
        equity_pct=Decimal("0.5"),
        quantity=Decimal(0),
    )


# --- an account with nothing left --------------------------------------------


def test_an_account_with_nothing_left_does_not_open_a_phantom_position():
    """Equity too small to buy anything cannot be sized from. A zero-unit
    position would put a row in the ledger that moved no money -- something
    that reads as a trade and is not one."""
    result = run_backtest(
        source_code=BUY_THEN_SELL,
        bars=_bars([100, 110, 120, 130]),
        assumptions=_percent(capital="0.000000001"),
    )

    assert all(trade.quantity > 0 for trade in result.trades)


# --- and the result says which sizing it used --------------------------------


def test_the_notes_describe_percent_sizing_rather_than_a_fixed_number():
    """The note said 「每次下單數量固定 1 單位」. Leaving that in place while the
    run sized off equity would describe a different experiment than the one
    that produced the numbers above it."""
    result = run_backtest(
        source_code=BUY_THEN_SELL,
        bars=_bars([100, 110, 120, 130]),
        assumptions=_percent(pct="0.5"),
    )

    # assumption_notes, not notes: the first is what the simulation charged
    # for, the second is what happened while it ran.
    notes = " ".join(result.assumption_notes)
    assert "50" in notes and "%" in notes, notes
    assert "固定 1 單位" not in notes


def test_the_notes_admit_that_lot_sizes_are_not_simulated():
    """Taiwanese round lots are 1,000 shares and this buys fractions. Rounding
    down to a whole lot on a NT$100,000 account would quietly turn most
    strategies into no-ops, so it is stated instead of done."""
    result = run_backtest(
        source_code=BUY_THEN_SELL, bars=_bars([100, 110, 120, 130]), assumptions=_percent()
    )

    assert any(
        "整股" in note or "零股" in note or "股數" in note for note in result.assumption_notes
    ), result.assumption_notes


def test_the_fixed_mode_note_is_unchanged():
    result = run_backtest(
        source_code=BUY_THEN_SELL,
        bars=_bars([100, 110, 120, 130]),
        assumptions=_free(quantity=Decimal(3)),
    )

    assert any("固定 3 單位" in note for note in result.assumption_notes), result.assumption_notes


# --- and it survives the round trip to the API -------------------------------


@pytest.fixture
def stub_market_data():
    """The same rising daily series tests/test_backtest_api.py uses, so the run
    actually produces a result and the echo assertion has something to read.
    Without it the endpoint answers 「no candles」 and the test proves nothing."""
    from app.main import app
    from app.models.enums import DataSource
    from app.services.market_data.service import MarketDataService, get_market_data_service

    class _Rising:
        data_source = DataSource.YFINANCE

        def get_quotes(self, symbols):
            return {}

        def get_bars(self, symbol, timeframe, limit):
            if symbol != "2330.TW":
                return []
            return [
                Bar(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=_START + timedelta(days=i),
                    open=100.0 + i,
                    high=102.0 + i,
                    low=99.0 + i,
                    close=101.0 + i,
                    volume=1000.0,
                )
                for i in range(40)
            ][-limit:]

    service = MarketDataService(providers={DataSource.YFINANCE: _Rising()})
    app.dependency_overrides[get_market_data_service] = lambda: service
    try:
        yield service
    finally:
        app.dependency_overrides.pop(get_market_data_service, None)


def _api_payload(**overrides) -> dict:
    payload = {
        "source_code": BUY_THEN_SELL.replace('"TEST"', '"2330.TW"'),
        "symbol": "2330.TW",
        "start": _START.isoformat(),
        "end": (_START + timedelta(days=39)).isoformat(),
    }
    payload.update(overrides)
    return payload


def test_the_api_echoes_the_sizing_it_ran_under(auth_client, stub_market_data):
    """A return figure whose sizing is not stated is not a number the owner can
    compare with anything."""
    resp = auth_client.post(
        "/api/backtests",
        json=_api_payload(position_sizing="percent_of_equity", equity_pct="0.25"),
    )

    assert resp.status_code == 201, resp.text
    assumptions = resp.json()["assumptions"]
    assert assumptions["position_sizing"] == "percent_of_equity"
    assert Decimal(assumptions["equity_pct"]) == Decimal("0.25")


def test_a_run_that_says_nothing_still_reports_the_old_fixed_sizing(auth_client, stub_market_data):
    """Every saved run predates this field. The response has to keep describing
    them the way they actually ran."""
    resp = auth_client.post("/api/backtests", json=_api_payload())

    assert resp.status_code == 201, resp.text
    assert resp.json()["assumptions"]["position_sizing"] == "fixed_quantity"


def test_the_api_refuses_a_fraction_above_one(auth_client, stub_market_data):
    resp = auth_client.post(
        "/api/backtests",
        json=_api_payload(position_sizing="percent_of_equity", equity_pct="1.5"),
    )

    assert resp.status_code == 422, resp.text


def test_the_percent_run_actually_sizes_off_the_capital(auth_client, stub_market_data):
    """End to end rather than at the service boundary: the request field has to
    reach BacktestAssumptions, or the echo above would be the only thing that
    changed."""
    resp = auth_client.post(
        "/api/backtests",
        json=_api_payload(position_sizing="percent_of_equity", equity_pct="1"),
    )

    assert resp.status_code == 201, resp.text
    trades = resp.json()["result"]["trades"]
    assert trades, resp.text
    # 100,000 of capital against a ~100 price is hundreds of units, not the
    # single unit the fixed default would have bought.
    assert Decimal(trades[0]["quantity"]) > 100
