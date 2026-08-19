"""Stop-loss and take-profit inside the replay.

The backtest existed to answer "would this strategy have made money", and it
answered it about a system the owner does not run. Live,
market_loop._check_position_exit watches every open position against the
stop-loss and take-profit percentages resolved for the strategy that opened
it, and files a SELL the moment either is crossed. The replay ignored both --
so a strategy configured to cut at -5% was scored as one that rides every loss
to the bottom, and the number it produced described a strategy that does not
exist.

That is precisely the failure the module's own docstring warns about: a
backtest of a parallel implementation manufactures confidence in the wrong
artefact.

WHY THIS ONE, WHEN THE OTHER RISK GATES STAY OUT. The position limit, the
capital cap, the cooldown and the pending-order cap are all evaluated against
state a replay of 2021 does not have -- the owner's positions *today*, their
orders pending *right now*, the wall clock. Stop-loss and take-profit read two
things only: the entry price of the position, and the price. The replay has
both. It is simulatable where the others are not, and that is the whole
distinction.

WHAT STILL HAS TO BE INVENTED, and is therefore stated in the assumptions: a
candle is not a path. When one candle's low crosses the stop AND its high
crosses the target, nothing in daily data says which came first. Assuming the
stop fired is the only choice that cannot flatter the result, so that is the
rule -- and every candle it was applied to is counted and reported, because a
strategy whose score rests on twenty such guesses has not really been tested.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.services.backtest import BacktestAssumptions, ExitReason, FillPriceBasis, run_backtest
from app.services.market_data.base import Bar, Timeframe

_START = datetime(2026, 1, 5, tzinfo=UTC)

# Costs off everywhere in this file: the arithmetic under test is which price
# the exit triggered at, and commission on top of it only obscures that. One
# test at the bottom puts the costs back to prove they still apply.
FREE = dict(
    commission_rate=Decimal(0),
    slippage_rate=Decimal(0),
    sell_tax_rate=Decimal(0),
)


def _bars(rows: list[tuple[float, float, float, float]]) -> list[Bar]:
    """Daily candles from explicit (open, high, low, close) tuples.

    Spelled out rather than derived from closes the way test_backtest.py does:
    every rule here is about where the high and the low went *within* a
    candle, so those two numbers are the subject, not scenery.
    """
    return [
        Bar(
            symbol="TEST",
            timeframe=Timeframe.DAY_1,
            timestamp=_START + timedelta(days=i),
            open=o,
            high=h,
            low=lo,
            close=c,
            volume=1000.0,
        )
        for i, (o, h, lo, c) in enumerate(rows)
    ]


def _strategy(*, buy_on: tuple[int, ...] = (1,), sell_on: tuple[int, ...] = ()) -> str:
    return f"""
class Strategy:
    def __init__(self):
        self.name = "exits"
        self.symbol = "TEST"
        self.timeframe = "1d"
        self.warmup_bars = 0
        self.seen = 0

    def on_bar(self, bar) -> str:
        self.seen += 1
        if self.seen in {buy_on!r}:
            return "BUY"
        if self.seen in {sell_on!r}:
            return "SELL"
        return "HOLD"
"""


def _run(rows, **kw):
    return run_backtest(
        source_code=_strategy(buy_on=kw.pop("buy_on", (1,)), sell_on=kw.pop("sell_on", ())),
        bars=_bars(rows),
        assumptions=BacktestAssumptions(**{**FREE, **kw}),
    )


# --- off by default, so nothing that already ran changes ---------------------


def test_no_stop_and_no_target_leaves_the_replay_exactly_as_it_was():
    """Both default to 0, the same "switched off" convention services/risk.py
    uses. A stored run from before this existed must still mean what it said."""
    result = _run([(100, 200, 10, 100)] * 4)

    assert result.summary.trade_count == 0, "nothing sold it, so it is still open"
    assert result.summary.open_quantity == Decimal(1)
    assert result.summary.stop_loss_exits == 0
    assert result.summary.take_profit_exits == 0


# --- the stop ---------------------------------------------------------------


def test_a_position_that_falls_past_the_stop_is_cut_without_the_strategy_saying_so():
    result = _run(
        [
            (100, 100, 100, 100),  # BUY signalled here
            (100, 101, 99, 100),  # filled at 100; stop is 90; low 99 holds
            (100, 101, 88, 95),  # low 88 crosses 90
        ],
        stop_loss_pct=Decimal("0.10"),
    )

    assert result.summary.stop_loss_exits == 1
    trade = result.trades[0]
    assert trade.exit_reason is ExitReason.STOP_LOSS
    assert trade.entry_price == Decimal(100)
    assert trade.exit_price == Decimal(90), "filled at the stop, not at the candle's low"
    assert trade.pnl == Decimal(-10)


def test_the_stop_fills_at_the_open_when_the_candle_gapped_straight_through_it():
    """No trade happened at 90, so no fill could have. Using the stop price
    here would credit the owner with an exit the market never offered -- the
    single most flattering error a backtest can make, because it turns every
    overnight collapse into a clean -10%."""
    result = _run(
        [
            (100, 100, 100, 100),
            (100, 101, 99, 100),
            (85, 86, 80, 82),  # opened below the stop
        ],
        stop_loss_pct=Decimal("0.10"),
    )

    assert result.trades[0].exit_price == Decimal(85)
    assert result.trades[0].pnl == Decimal(-15)


def test_a_stop_can_fire_on_the_very_candle_the_position_opened_on():
    """The fill lands on this candle's open and the price then falls through
    the stop before the close -- which live is simply two quotes apart."""
    result = _run(
        [
            (100, 100, 100, 100),
            (100, 101, 85, 90),  # bought at 100 here, then straight down
        ],
        stop_loss_pct=Decimal("0.10"),
    )

    trade = result.trades[0]
    assert trade.opened_at == trade.closed_at
    assert trade.exit_price == Decimal(90)


def test_a_stop_of_zero_is_off_rather_than_selling_at_the_entry_price():
    """services/risk.py reads 0 as "switched off" and this must agree with it,
    or the same number would mean opposite things in the two places the owner
    types it."""
    result = _run(
        [(100, 100, 100, 100), (100, 101, 99, 100), (100, 101, 50, 60)],
        stop_loss_pct=Decimal(0),
    )

    assert result.summary.stop_loss_exits == 0


# --- the target -------------------------------------------------------------


def test_a_position_that_rises_past_the_target_is_taken():
    result = _run(
        [
            (100, 100, 100, 100),
            (100, 101, 99, 100),
            (100, 115, 99, 112),  # high 115 crosses 110
        ],
        take_profit_pct=Decimal("0.10"),
    )

    assert result.summary.take_profit_exits == 1
    trade = result.trades[0]
    assert trade.exit_reason is ExitReason.TAKE_PROFIT
    assert trade.exit_price == Decimal(110)
    assert trade.pnl == Decimal(10)


def test_a_gap_above_the_target_really_does_fill_above_it():
    """Not optimism: if it opens at 120 there is no 110 left to sell at, and
    the owner's order fills at 120. The symmetric case of the gap-down."""
    result = _run(
        [
            (100, 100, 100, 100),
            (100, 101, 99, 100),
            (120, 125, 118, 122),
        ],
        take_profit_pct=Decimal("0.10"),
    )

    assert result.trades[0].exit_price == Decimal(120)


# --- the part a candle cannot answer ----------------------------------------


def test_when_one_candle_touches_both_the_stop_is_assumed_to_have_fired_first():
    """Daily data records four numbers, not a path. Either answer is a guess,
    and only one of them cannot flatter the result."""
    result = _run(
        [
            (100, 100, 100, 100),
            (100, 101, 99, 100),
            (100, 115, 88, 100),  # touched 110 and 90; order unknown
        ],
        stop_loss_pct=Decimal("0.10"),
        take_profit_pct=Decimal("0.10"),
    )

    assert result.trades[0].exit_reason is ExitReason.STOP_LOSS
    assert result.trades[0].exit_price == Decimal(90)


def test_a_guessed_exit_is_counted_and_said_out_loud():
    """A result resting on twenty coin-flips has not been tested, and the
    owner cannot know that from the return figure alone."""
    result = _run(
        [
            (100, 100, 100, 100),
            (100, 101, 99, 100),
            (100, 115, 88, 100),
        ],
        stop_loss_pct=Decimal("0.10"),
        take_profit_pct=Decimal("0.10"),
    )

    assert result.summary.ambiguous_exit_bars == 1
    assert any("停損" in note and "無法確定" in note for note in result.notes), result.notes


def test_a_clean_run_says_nothing_about_ambiguity():
    """The warning has to stay rare to stay readable."""
    result = _run(
        [(100, 100, 100, 100), (100, 101, 99, 100), (100, 101, 88, 95)],
        stop_loss_pct=Decimal("0.10"),
        take_profit_pct=Decimal("0.50"),
    )

    assert result.summary.ambiguous_exit_bars == 0
    assert not any("無法確定" in note for note in result.notes)


# --- how it fits with the strategy's own signals ----------------------------


def test_the_strategy_can_buy_back_in_after_being_stopped_out():
    result = _run(
        [
            (100, 100, 100, 100),  # 1: BUY
            (100, 101, 99, 100),  # 2: filled at 100
            (100, 101, 88, 95),  # 3: stopped at 90
            (95, 96, 94, 95),  # 4: BUY again
            (95, 96, 94, 95),  # 5: filled at 95
        ],
        buy_on=(1, 4),
        stop_loss_pct=Decimal("0.10"),
    )

    assert result.summary.trade_count == 1
    assert result.summary.open_quantity == Decimal(1)
    assert result.summary.open_avg_entry_price == Decimal(95)


def test_a_sell_signal_after_the_stop_already_fired_is_skipped_not_doubled():
    """Live it would be refused as a sale with no position behind it; here it
    must not invent a short."""
    result = _run(
        [
            (100, 100, 100, 100),  # 1: BUY
            (100, 101, 99, 100),  # 2: filled
            (100, 101, 88, 95),  # 3: stopped, then the strategy says SELL
            (95, 96, 94, 95),
        ],
        sell_on=(3,),
        stop_loss_pct=Decimal("0.10"),
    )

    assert result.summary.trade_count == 1
    assert result.summary.skipped_signals == 1


def test_a_strategys_own_exit_is_still_labelled_as_its_own():
    """Which exits were the strategy's decision and which were the stop is the
    thing the owner is trying to learn -- "my rules make money but the stop
    keeps cutting them" is invisible if both are just 'sold'."""
    result = _run(
        [
            (100, 100, 100, 100),
            (100, 101, 99, 100),
            (105, 106, 104, 105),  # 3: SELL
            (105, 106, 104, 105),  # filled at 105
        ],
        sell_on=(3,),
        stop_loss_pct=Decimal("0.50"),
    )

    assert result.trades[0].exit_reason is ExitReason.SIGNAL
    assert result.summary.stop_loss_exits == 0


def test_the_stop_is_checked_before_the_strategy_is_asked_for_this_candle():
    """The strategy decides at the close; the stop triggers wherever in the
    candle the price reached it, which is never later than the close. Getting
    this order wrong would let a strategy sell at a price it was already
    stopped out of."""
    result = _run(
        [
            (100, 100, 100, 100),
            (100, 101, 99, 100),
            (100, 101, 88, 100),  # stop at 90 crossed; strategy says SELL at close 100
        ],
        sell_on=(3,),
        stop_loss_pct=Decimal("0.10"),
    )

    assert result.trades[0].exit_price == Decimal(90), "not the 100 the close would have paid"


# --- the exit is a real trade, so it pays real costs ------------------------


def test_a_stop_exit_pays_the_same_commission_slippage_and_tax_as_any_sale():
    """It is a market sale like any other. Charging nothing for it would make
    a stop-heavy strategy look cheaper to run than it is."""
    result = run_backtest(
        source_code=_strategy(),
        bars=_bars([(100, 100, 100, 100), (100, 101, 99, 100), (100, 101, 88, 95)]),
        assumptions=BacktestAssumptions(
            commission_rate=Decimal("0.001425"),
            slippage_rate=Decimal("0.0005"),
            sell_tax_rate=Decimal("0.003"),
            stop_loss_pct=Decimal("0.10"),
        ),
    )

    assert result.summary.stop_loss_exits == 1

    # The stop hangs off the price the position actually cost, which already
    # carries slippage and commission -- exactly as live, where the threshold
    # is measured against position.avg_entry_price and not against the price
    # on the screen when the order was sent. So it is 90% of 100.19..., not
    # of 100.
    entry = Decimal(100) * (Decimal(1) + Decimal("0.0005")) * (Decimal(1) + Decimal("0.001425"))
    assert abs(result.trades[0].entry_price - entry) < Decimal("0.0001")

    threshold = result.trades[0].entry_price * Decimal("0.90")
    expected = (
        threshold
        * (Decimal(1) - Decimal("0.0005"))
        * (Decimal(1) - Decimal("0.001425") - Decimal("0.003"))
    )
    assert abs(result.trades[0].exit_price - expected) < Decimal("0.0001")
    assert result.trades[0].exit_price < threshold, "the exit pays to get out too"
    assert result.summary.total_costs > 0


# --- what the owner is told -------------------------------------------------


def test_the_assumptions_say_the_stop_is_being_simulated_and_at_what_level():
    result = _run(
        [(100, 100, 100, 100)] * 3,
        stop_loss_pct=Decimal("0.10"),
        take_profit_pct=Decimal("0.20"),
    )

    joined = "\n".join(result.assumption_notes)
    assert "10%" in joined and "20%" in joined
    assert "停損" in joined and "停利" in joined


def test_the_assumptions_still_say_so_when_they_are_switched_off():
    """Silence would read as "it was applied"; the old note said plainly that
    it was not, and that has to survive."""
    joined = "\n".join(_run([(100, 100, 100, 100)] * 3).assumption_notes)

    assert "沒有模擬停損" in joined or "不模擬停損" in joined


def test_a_negative_threshold_is_refused_rather_than_silently_inverted():
    with pytest.raises(ValueError):
        BacktestAssumptions(stop_loss_pct=Decimal("-0.1"))
    with pytest.raises(ValueError):
        BacktestAssumptions(take_profit_pct=Decimal("-0.1"))


def test_close_basis_entries_are_guarded_too():
    """Under CLOSE the position opens at the signal candle's close, so the
    first candle that can stop it out is the next one -- not the one it was
    bought on, whose low is already in the past."""
    result = _run(
        [
            (100, 100, 85, 100),  # BUY at this close; the 85 low is BEFORE the entry
            (100, 101, 88, 95),  # first candle that can stop it
        ],
        fill_price_basis=FillPriceBasis.CLOSE,
        stop_loss_pct=Decimal("0.10"),
    )

    assert result.summary.stop_loss_exits == 1
    assert result.trades[0].opened_at != result.trades[0].closed_at
