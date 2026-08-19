"""Numbers that make a backtest result decidable.

A total return on its own does not tell the owner whether a strategy is worth
running. Three things were missing and each one changes the answer:

- **No benchmark.** +18% looks good until you learn the stock itself did +40%
  over the same period, at which point the strategy destroyed value. In a
  bull run nearly anything is profitable, and without buy-and-hold beside it
  there is no way to separate "the strategy works" from "the market went up".
  The close of every bar was already being recorded and thrown away.

- **No profit factor, Sharpe or exposure.** A 60% win rate that makes 1 and
  loses 5 is a losing strategy, and win rate alone says the opposite. Nothing
  said how much volatility the return cost, or how much of the period the
  money was even at risk.

- **Silent truncation.** yfinance caps history by interval -- five years for
  daily, 60 days for 5-minute -- and the result reported back the range that
  was *asked for*. Somebody could believe they had validated a strategy
  across 2015 when the run never saw a bar before 2021.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.services.backtest import BacktestAssumptions, run_backtest
from app.services.market_data.base import Bar, Timeframe

RISING = """
class Strategy:
    def __init__(self):
        self.name = "buy_once"
        self.symbol = "TEST"
        self.bought = False

    def on_tick(self, current_price: float) -> str:
        if not self.bought:
            self.bought = True
            return "BUY"
        return "HOLD"
"""

NEVER_TRADES = """
class Strategy:
    def __init__(self):
        self.name = "sits_out"
        self.symbol = "TEST"

    def on_tick(self, current_price: float) -> str:
        return "HOLD"
"""


def _bars(closes: list[float], start: datetime | None = None) -> list[Bar]:
    begin = start or datetime(2026, 1, 1, tzinfo=UTC)
    return [
        Bar(
            symbol="TEST",
            timeframe=Timeframe.DAY_1,
            timestamp=begin + timedelta(days=i),
            open=Decimal(str(close)),
            high=Decimal(str(close)),
            low=Decimal(str(close)),
            close=Decimal(str(close)),
            volume=Decimal(1000),
        )
        for i, close in enumerate(closes)
    ]


def _free() -> BacktestAssumptions:
    """No costs, so the arithmetic under test is the only thing moving."""
    return BacktestAssumptions(
        commission_rate=Decimal(0),
        slippage_rate=Decimal(0),
        sell_tax_rate=Decimal(0),
        minimum_fee=Decimal(0),
        quantity=Decimal(1),
        initial_capital=Decimal(10000),
    )


def _run(closes: list[float], source: str = RISING):
    return run_backtest(
        source_code=source,
        bars=_bars(closes),
        assumptions=_free(),
        stored_warmup_bars=0,
    )


# --- buy and hold -----------------------------------------------------------


def test_the_result_says_what_simply_holding_would_have_done():
    # 100 -> 200 is a straight double for anyone who did nothing.
    result = _run([100, 120, 150, 200], source=NEVER_TRADES)
    assert result.summary.buy_and_hold_return_pct == Decimal(100)


def test_buy_and_hold_is_measured_over_the_bars_actually_tested():
    """Not the requested range, and not including warm-up: the comparison is
    only fair against the same period the strategy traded in."""
    result = _run([100, 50], source=NEVER_TRADES)
    assert result.summary.buy_and_hold_return_pct == Decimal(-50)


def test_a_strategy_that_beats_the_market_shows_a_positive_edge():
    result = _run([100, 120, 150, 200], source=NEVER_TRADES)
    # It did nothing, so it is exactly buy-and-hold behind by its own return.
    assert result.summary.excess_return_pct == (
        result.summary.total_return_pct - result.summary.buy_and_hold_return_pct
    )


def test_buy_and_hold_is_none_when_there_is_nothing_to_compare():
    result = run_backtest(
        source_code=NEVER_TRADES, bars=[], assumptions=_free(), stored_warmup_bars=0
    )
    assert result.summary.buy_and_hold_return_pct is None
    assert result.summary.excess_return_pct is None


# --- profit factor ----------------------------------------------------------


def test_profit_factor_answers_what_win_rate_cannot():
    """Win often, lose big: 3 wins of 1 against 1 loss of 5 is a 75% win rate
    and a losing strategy."""
    from app.services.backtest import _profit_factor

    assert _profit_factor(Decimal(3), Decimal(5)) < 1
    assert _profit_factor(Decimal(10), Decimal(5)) == Decimal(2)


def test_profit_factor_is_none_rather_than_infinite_when_nothing_lost():
    """A strategy with no losing trade has no ratio. Reporting a huge number
    would read as a great strategy rather than as too few trades to judge."""
    from app.services.backtest import _profit_factor

    assert _profit_factor(Decimal(10), Decimal(0)) is None
    assert _profit_factor(Decimal(0), Decimal(0)) is None


# --- exposure ---------------------------------------------------------------


def test_exposure_says_how_much_of_the_time_the_money_was_at_risk():
    """A 10% return earned while invested a tenth of the time is a very
    different strategy from one fully invested throughout."""
    result = _run([100, 110, 120, 130])
    # RISING buys on the first bar and never sells, so it is in the market for
    # every bar after the entry.
    assert result.summary.exposure_pct is not None
    assert result.summary.exposure_pct > Decimal(50)


def test_a_strategy_that_never_trades_has_no_exposure():
    result = _run([100, 110, 120], source=NEVER_TRADES)
    assert result.summary.exposure_pct == Decimal(0)


# --- truncation -------------------------------------------------------------


def test_the_result_reports_the_range_it_actually_covered():
    """Providers cap history by interval and silently return less. The run has
    to say what it really saw, or somebody validates a strategy across 2015
    that never saw a bar before 2021."""
    result = _run([100, 110, 120])
    assert result.first_bar_at is not None
    assert result.last_bar_at is not None
    assert result.first_bar_at < result.last_bar_at


# --- saying so when the data was not there ---------------------------------


def test_a_short_history_is_called_out_rather_than_passed_off_as_the_range():
    """yfinance caps history by interval -- five years of daily, 60 days of
    5-minute -- and returns less without saying so. The result used to echo
    back the range that was *asked for*, so somebody could believe they had
    validated a strategy across 2015 when no bar before 2021 ever reached it.
    """
    from app.services.backtest import truncation_note

    asked_start = datetime(2015, 1, 1, tzinfo=UTC)
    asked_end = datetime(2026, 8, 1, tzinfo=UTC)
    got_first = datetime(2021, 8, 1, tzinfo=UTC)
    got_last = datetime(2026, 8, 1, tzinfo=UTC)

    note = truncation_note(asked_start, asked_end, got_first, got_last)
    assert note is not None
    assert "2015" in note and "2021" in note


def test_no_note_when_the_data_covers_what_was_asked_for():
    from app.services.backtest import truncation_note

    asked_start = datetime(2026, 1, 1, tzinfo=UTC)
    asked_end = datetime(2026, 8, 1, tzinfo=UTC)
    assert truncation_note(asked_start, asked_end, asked_start, asked_end) is None


def test_a_few_days_of_slack_is_not_worth_a_warning():
    """Weekends and holidays mean the first bar is almost never exactly the
    requested date. Warning every time would train the owner to ignore it."""
    from app.services.backtest import truncation_note

    asked_start = datetime(2026, 1, 1, tzinfo=UTC)
    asked_end = datetime(2026, 8, 1, tzinfo=UTC)
    got_first = datetime(2026, 1, 5, tzinfo=UTC)  # the Monday after
    assert truncation_note(asked_start, asked_end, got_first, asked_end) is None


def test_no_note_when_there_were_no_bars_at_all():
    """That case already has its own, clearer message."""
    from app.services.backtest import truncation_note

    asked_start = datetime(2026, 1, 1, tzinfo=UTC)
    asked_end = datetime(2026, 8, 1, tzinfo=UTC)
    assert truncation_note(asked_start, asked_end, None, None) is None
