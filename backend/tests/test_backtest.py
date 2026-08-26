"""Replaying a strategy over historical candles.

The rules pinned here are the ones that decide whether a backtest is worth
reading at all:

* it drives the strategy through the SAME runtime the live loop uses -- same
  sandbox, same on_bar/on_tick dispatch, same warm-up rule, same `indicators`
  namespace. A backtest of a parallel implementation describes code the owner
  does not run;
* at bar N the strategy sees bars 0..N and nothing beyond, and a fill is never
  marked into the equity curve before the bar it could have happened on;
* every cost the simulation charges (and every one it does not) is stated in
  the result.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.enums import DataSource
from app.services.backtest import (
    BacktestAssumptions,
    BacktestError,
    FillPriceBasis,
    estimated_bar_count,
    load_backtest_bars,
    run_backtest,
)
from app.services.market_data.base import Bar, Timeframe
from app.services.market_data.service import MarketDataService

_START = datetime(2026, 1, 5, tzinfo=UTC)

# Costs off, so a test about *mechanics* asserts round numbers and a test
# about costs is the only place their arithmetic appears.
FREE = BacktestAssumptions(
    commission_rate=Decimal(0),
    slippage_rate=Decimal(0),
    sell_tax_rate=Decimal(0),
)


def _bars(
    closes: list[float],
    *,
    opens: list[float] | None = None,
    symbol: str = "TEST",
    timeframe: Timeframe = Timeframe.DAY_1,
    start: datetime = _START,
) -> list[Bar]:
    """Daily candles closing at `closes`, one per day from `start`."""
    return [
        Bar(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=start + timedelta(days=i),
            open=(opens[i] if opens is not None else close),
            high=max(close, opens[i] if opens is not None else close) + 1,
            low=min(close, opens[i] if opens is not None else close) - 1,
            close=close,
            volume=1000.0,
        )
        for i, close in enumerate(closes)
    ]


# --- strategies used by the tests -------------------------------------------

BUY_ON_FIFTH_BAR = """
class Strategy:
    def __init__(self):
        self.name = "nth_bar"
        self.symbol = "TEST"
        self.timeframe = "1d"
        self.warmup_bars = 3
        self.seen = 0

    def on_bar(self, bar) -> str:
        self.seen += 1
        return "BUY" if self.seen == 5 else "HOLD"
"""

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

ALWAYS_BUY = """
class Strategy:
    def __init__(self):
        self.name = "greedy"
        self.symbol = "TEST"
        self.timeframe = "1d"
        self.warmup_bars = 0

    def on_bar(self, bar) -> str:
        return "BUY"
"""

ALWAYS_SELL = """
class Strategy:
    def __init__(self):
        self.name = "seller"
        self.symbol = "TEST"
        self.timeframe = "1d"
        self.warmup_bars = 0

    def on_bar(self, bar) -> str:
        return "SELL"
"""

BUY_ON_THIRD_BAR = """
class Strategy:
    def __init__(self):
        self.name = "late_buyer"
        self.symbol = "TEST"
        self.timeframe = "1d"
        self.warmup_bars = 0
        self.seen = 0

    def on_bar(self, bar) -> str:
        self.seen += 1
        return "BUY" if self.seen == 3 else "HOLD"
"""

# The look-ahead probe. The series it is replayed over only ever rises, so a
# close higher than the current bar's is by definition a bar that has not
# happened yet. If the harness ever replays the range out of order -- warming
# up on the whole series before asking for a decision, feeding newest-first,
# handing over a slice -- this fires.
LOOKAHEAD_PROBE = """
class Strategy:
    def __init__(self):
        self.name = "peeker"
        self.symbol = "TEST"
        self.timeframe = "1d"
        self.warmup_bars = 2
        self.highest_seen = None

    def on_bar(self, bar) -> str:
        if self.highest_seen is not None and self.highest_seen > bar.close:
            return "BUY"
        self.highest_seen = bar.close
        return "HOLD"
"""

TICK_STRATEGY = """
class Strategy:
    def __init__(self):
        self.name = "ticker"
        self.symbol = "TEST"
        self.prices = []

    def on_tick(self, current_price: float) -> str:
        self.prices.append(current_price)
        if len(self.prices) == 2:
            return "BUY"
        if len(self.prices) == 4:
            return "SELL"
        return "HOLD"
"""


# --- the same runtime the live loop uses ------------------------------------


def test_the_backtest_refuses_source_the_live_sandbox_refuses():
    """Same sandbox, not a looser one. A strategy that only backtests because
    the replay let it reach the filesystem is a strategy the owner can never
    actually run.

    斷言的是**理由**，不是例外型別。策略搬進子行程之後（#18），
    StrategyValidationError 到不了這裡——例外送不過 JSON 管線，只有文字過得來。
    改問理由其實比原本強：使用者讀到的就是那句話，而型別他永遠看不到。
    """
    with pytest.raises(BacktestError, match="importing 'os' is not allowed"):
        run_backtest(
            source_code="import os\n" + ALWAYS_BUY,
            bars=_bars([100.0, 101.0]),
            assumptions=FREE,
        )


def test_the_backtest_gives_the_strategy_the_same_indicator_namespace():
    source = """
class Strategy:
    def __init__(self):
        self.name = "sma_user"
        self.symbol = "TEST"
        self.timeframe = "1d"
        self.warmup_bars = 0
        self.closes = []

    def on_bar(self, bar) -> str:
        self.closes.append(bar.close)
        average = indicators.sma(self.closes, 3)[-1]
        return "BUY" if average is not None and bar.close > average else "HOLD"
"""
    result = run_backtest(
        source_code=source, bars=_bars([10.0, 11.0, 12.0, 13.0, 14.0]), assumptions=FREE
    )

    # It ran at all, which is the point: `indicators` resolved inside the
    # sandbox exactly as it does on a live tick.
    assert result.summary.bars_tested == 5
    assert result.summary.signals > 0


def test_the_warm_up_rule_matches_the_live_loop():
    """`self.warmup_bars` candles are replayed to fill the strategy's memory
    and their signals are thrown away, exactly as market_loop does on the
    first poll -- so decisions start at index warmup, and the warm-up candles
    still count toward the strategy's own state."""
    bars = _bars([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])

    result = run_backtest(source_code=BUY_ON_FIFTH_BAR, bars=bars, assumptions=FREE)

    assert result.warmup_bars == 3
    assert result.summary.bars_tested == 3  # bars 3, 4, 5
    # seen==5 lands on bars[4]: three warm-up candles plus bars[3] and bars[4].
    assert result.summary.signals == 1
    assert [t.opened_at for t in result.trades] == []  # never closed
    assert result.summary.open_quantity == Decimal(1)
    # Filled at the open of the bar AFTER the signal -- bars[5].
    assert result.summary.open_avg_entry_price == Decimal(105)


def test_a_strategy_declaring_no_warm_up_falls_back_to_the_stored_default():
    """Same precedence as the live loop's, because it is literally the same
    function (strategy_runtime.effective_warmup): the source wins, and only
    source that says nothing defers to the stored column."""
    source = ALWAYS_BUY.replace("        self.warmup_bars = 0\n", "")

    result = run_backtest(
        source_code=source, bars=_bars([100.0] * 6), assumptions=FREE, stored_warmup_bars=4
    )

    assert result.warmup_bars == 4
    assert result.summary.bars_tested == 2


def test_a_range_shorter_than_the_warm_up_tests_nothing_and_says_so():
    result = run_backtest(
        source_code=BUY_ON_FIFTH_BAR, bars=_bars([100.0, 101.0]), assumptions=FREE
    )

    assert result.summary.bars_tested == 0
    assert result.trades == []
    assert any("暖身" in note for note in result.notes)


def test_a_strategy_that_raises_stops_the_run_with_the_bar_it_died_on():
    source = """
class Strategy:
    def __init__(self):
        self.name = "boom"
        self.symbol = "TEST"
        self.timeframe = "1d"
        self.warmup_bars = 0

    def on_bar(self, bar) -> str:
        raise RuntimeError("boom")
"""
    with pytest.raises(BacktestError) as excinfo:
        run_backtest(source_code=source, bars=_bars([100.0, 101.0]), assumptions=FREE)

    assert "boom" in str(excinfo.value)


# --- look-ahead bias --------------------------------------------------------


def test_a_strategy_is_never_shown_a_bar_from_the_future():
    """The whole point of a backtest. Replayed over a series that only rises,
    a strategy that reports having seen a higher close than the current bar's
    has been shown a candle that had not happened yet."""
    rising = _bars([100.0 + i for i in range(40)])

    result = run_backtest(source_code=LOOKAHEAD_PROBE, bars=rising, assumptions=FREE)

    assert result.summary.bars_tested == 38
    assert result.summary.signals == 0
    assert result.summary.trade_count == 0
    assert result.summary.open_quantity == Decimal(0)


def test_a_fill_is_not_marked_into_equity_before_the_bar_it_happens_on():
    """Under the default next-open basis, a signal on bar N is an order the
    owner places after the close and that executes on bar N+1. Equity at bar N
    must therefore still show a flat book -- crediting it at N would be the
    backtest itself peeking one bar ahead."""
    bars = _bars([100.0, 100.0, 100.0, 100.0], opens=[100.0, 100.0, 100.0, 100.0])

    result = run_backtest(source_code=BUY_THEN_SELL, bars=bars, assumptions=FREE)

    by_timestamp = {point.timestamp: point for point in result.equity_curve}
    assert by_timestamp[bars[0].timestamp].position_qty == Decimal(0)  # signal bar
    assert by_timestamp[bars[1].timestamp].position_qty == Decimal(1)  # fill bar


def test_bars_outside_the_requested_range_never_reach_the_strategy():
    provider = _StubBarProvider(_bars([100.0 + i for i in range(10)]))
    service = MarketDataService(providers={DataSource.YFINANCE: provider})

    loaded = load_backtest_bars(
        service,
        symbol="TEST",
        timeframe=Timeframe.DAY_1,
        data_source=DataSource.YFINANCE,
        start=_START + timedelta(days=2),
        end=_START + timedelta(days=5),
    )

    assert [bar.timestamp for bar in loaded] == [
        _START + timedelta(days=offset) for offset in (2, 3, 4, 5)
    ]


# --- fills, costs and the assumptions behind them ---------------------------


def test_the_default_fill_basis_is_the_next_bars_open():
    """The honest default: the owner sees a candle close, then places an
    order, which executes at the next open. Filling at the close they only
    just observed is a price they could not have traded at."""
    bars = _bars([100.0, 200.0, 300.0], opens=[100.0, 210.0, 320.0])

    result = run_backtest(source_code=BUY_THEN_SELL, bars=bars, assumptions=FREE)

    assert result.assumptions.fill_price_basis is FillPriceBasis.NEXT_OPEN
    assert result.summary.open_avg_entry_price == Decimal(210)


def test_the_close_basis_fills_at_the_signal_bars_own_close():
    bars = _bars([100.0, 200.0, 300.0], opens=[100.0, 210.0, 320.0])

    result = run_backtest(
        source_code=BUY_THEN_SELL,
        bars=bars,
        assumptions=BacktestAssumptions(
            fill_price_basis=FillPriceBasis.CLOSE,
            commission_rate=Decimal(0),
            slippage_rate=Decimal(0),
            sell_tax_rate=Decimal(0),
        ),
    )

    assert result.trades[0].entry_price == Decimal("100.00000000")
    assert result.trades[0].exit_price == Decimal("300.00000000")


def test_a_signal_on_the_last_bar_has_nothing_to_fill_against():
    """Under the next-open basis the newest candle's signal has no bar left to
    execute on. Pretending it filled at that candle's own close would be the
    optimism this basis exists to remove, so it is reported unfilled."""
    bars = _bars([100.0, 101.0, 102.0])

    result = run_backtest(source_code=BUY_ON_THIRD_BAR, bars=bars, assumptions=FREE)

    assert result.summary.open_quantity == Decimal(0)
    assert result.summary.unfilled_signals == 1
    assert any("最後一根" in note for note in result.notes)


def test_costs_are_charged_against_the_owner_on_both_sides():
    """Slippage always moves the price the wrong way, commission is paid on
    entry and exit, and the sell-side tax is charged on the way out only."""
    bars = _bars([100.0, 100.0, 100.0, 100.0], opens=[100.0, 100.0, 100.0, 110.0])
    assumptions = BacktestAssumptions(
        commission_rate=Decimal("0.005"),
        slippage_rate=Decimal("0.01"),
        sell_tax_rate=Decimal("0.002"),
    )

    result = run_backtest(source_code=BUY_THEN_SELL, bars=bars, assumptions=assumptions)

    trade = result.trades[0]
    # BUY at the open of bars[1]: 100 * (1 + 0.01) * (1 + 0.005)
    assert trade.entry_price == Decimal("101.50500000")
    # SELL at the open of bars[3]: 110 * (1 - 0.01) * (1 - 0.005 - 0.002)
    assert trade.exit_price == Decimal("108.13770000")
    assert trade.pnl == Decimal("6.63270000")
    assert result.summary.total_costs > 0


def test_the_result_states_what_it_assumed():
    """A backtest that quotes its numbers without its costs is a lie of
    omission, so the assumptions travel with the result in the owner's own
    language."""
    result = run_backtest(source_code=BUY_THEN_SELL, bars=_bars([100.0] * 4))

    prose = "\n".join(result.assumption_notes)
    assert "手續費" in prose
    assert "滑價" in prose
    assert "交易稅" in prose
    assert "風控" in prose  # whether the live gates applied has to be stated
    assert result.assumptions.commission_rate > 0  # an honest default, not zero


def test_zero_costs_are_still_stated_rather_than_left_unsaid():
    result = run_backtest(source_code=BUY_THEN_SELL, bars=_bars([100.0] * 4), assumptions=FREE)

    assert any("0%" in note or "0 " in note for note in result.assumption_notes)


# --- what the simulated account may do --------------------------------------


def test_a_buy_while_already_holding_is_skipped_and_counted():
    """One position at a time. The live path would not stack either -- a
    second pending BUY for the same symbol is refused while the first waits
    for the owner to confirm it."""
    result = run_backtest(source_code=ALWAYS_BUY, bars=_bars([100.0] * 6), assumptions=FREE)

    assert result.summary.open_quantity == Decimal(1)
    assert result.summary.skipped_signals >= 3
    assert any("已有部位" in note for note in result.notes)


def test_a_sell_with_nothing_held_is_skipped_and_counted():
    """Mirrors portfolio.ensure_fill_applicable: the ledger refuses to sell
    what is not held rather than opening a short."""
    result = run_backtest(source_code=ALWAYS_SELL, bars=_bars([100.0] * 5), assumptions=FREE)

    assert result.trades == []
    assert result.summary.open_quantity == Decimal(0)
    assert result.summary.skipped_signals == 5
    assert any("沒有部位" in note for note in result.notes)


def test_an_on_tick_strategy_is_replayed_on_candle_closes_and_told_so():
    """The live loop drives on_tick from quotes, which history does not have.
    Bar closes are the honest stand-in -- and the result says so instead of
    letting the owner assume tick-level fidelity."""
    bars = _bars([100.0, 100.0, 100.0, 100.0, 100.0], opens=[100.0, 100.0, 100.0, 100.0, 120.0])

    result = run_backtest(source_code=TICK_STRATEGY, bars=bars, assumptions=FREE)

    assert result.entry_point == "on_tick"
    assert result.summary.trade_count == 1
    assert result.trades[0].pnl == Decimal("20.00000000")
    assert any("on_tick" in note for note in result.assumption_notes)


# --- the numbers a retail trader reads --------------------------------------


def test_the_summary_reports_the_numbers_a_retail_trader_reads():
    """One winner (+10) and one loser (-5) on a fixed 1-unit size."""
    source = """
class Strategy:
    def __init__(self):
        self.name = "two_trades"
        self.symbol = "TEST"
        self.timeframe = "1d"
        self.warmup_bars = 0
        self.seen = 0

    def on_bar(self, bar) -> str:
        self.seen += 1
        if self.seen in (1, 5):
            return "BUY"
        if self.seen in (3, 7):
            return "SELL"
        return "HOLD"
"""
    # Fills land on bars 1, 3, 5 and 7: buy 100, sell 110 (+10), buy 110,
    # sell 105 (-5).
    opens = [100.0, 100.0, 100.0, 110.0, 110.0, 110.0, 110.0, 105.0, 105.0]
    bars = _bars([100.0] * 9, opens=opens)

    result = run_backtest(
        source_code=source,
        bars=bars,
        assumptions=BacktestAssumptions(
            commission_rate=Decimal(0),
            slippage_rate=Decimal(0),
            sell_tax_rate=Decimal(0),
            initial_capital=Decimal(1000),
        ),
    )

    summary = result.summary
    assert summary.trade_count == 2
    assert summary.wins == 1
    assert summary.losses == 1
    assert summary.win_rate_pct == Decimal("50.0000")
    assert summary.average_win == Decimal("10.00000000")
    assert summary.average_loss == Decimal("-5.00000000")
    assert summary.net_pnl == Decimal("5.00000000")
    assert summary.final_equity == Decimal("1005.00000000")
    assert summary.total_return_pct == Decimal("0.5000")


def test_max_drawdown_is_measured_from_the_equity_peak():
    """Bought at 100, the position is marked at each close: 120 (peak), then
    80, then back to 110. Against a 1000 baseline the trough equity is 980
    from a peak of 1020, i.e. 3.9216%."""
    bars = _bars([100.0, 120.0, 80.0, 110.0], opens=[100.0, 100.0, 100.0, 100.0])

    result = run_backtest(
        source_code=ALWAYS_BUY,
        bars=bars,
        assumptions=BacktestAssumptions(
            commission_rate=Decimal(0),
            slippage_rate=Decimal(0),
            sell_tax_rate=Decimal(0),
            initial_capital=Decimal(1000),
        ),
    )

    assert result.summary.max_drawdown_pct == Decimal("3.9216")


def test_the_equity_curve_has_one_point_per_tested_bar():
    bars = _bars([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])

    result = run_backtest(source_code=BUY_ON_FIFTH_BAR, bars=bars, assumptions=FREE)

    assert len(result.equity_curve) == result.summary.bars_tested
    assert [point.timestamp for point in result.equity_curve] == [bar.timestamp for bar in bars[3:]]
    assert all(point.equity > 0 for point in result.equity_curve)


def test_a_run_with_no_trades_reports_zero_rather_than_dividing_by_none():
    result = run_backtest(source_code=LOOKAHEAD_PROBE, bars=_bars([100.0] * 8), assumptions=FREE)

    assert result.summary.trade_count == 0
    assert result.summary.win_rate_pct is None
    assert result.summary.average_win is None
    assert result.summary.average_loss is None
    assert result.summary.max_drawdown_pct == Decimal("0.0000")


# --- bounding the work ------------------------------------------------------


@pytest.mark.parametrize(
    ("timeframe", "days", "expected"),
    [
        (Timeframe.MINUTE_1, 1, 1440),
        (Timeframe.DAY_1, 365, 365),
        (Timeframe.WEEK_1, 70, 10),
    ],
)
def test_estimated_bar_count_bounds_the_request_before_any_fetch(timeframe, days, expected):
    assert estimated_bar_count(_START, _START + timedelta(days=days), timeframe) == expected


class _StubBarProvider:
    """Serves a fixed candle series and counts fetches, honouring `limit` the
    way a real provider does."""

    data_source = DataSource.YFINANCE

    def __init__(self, series: list[Bar]) -> None:
        self.series = series
        self.limits: list[int] = []

    def get_quotes(self, symbols):
        return {}

    def get_bars(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Bar]:
        self.limits.append(limit)
        return list(self.series)[-limit:]


def test_a_deep_backtest_is_not_starved_by_the_live_loops_shallow_cache():
    """The market loop only ever caches its own 300-candle window. A backtest
    asking for more must refetch instead of silently being handed the shallow
    history and reporting on a shorter range than the owner asked for."""
    series = _bars([100.0 + i for i in range(500)], start=_START - timedelta(days=500))
    provider = _StubBarProvider(series)
    service = MarketDataService(providers={DataSource.YFINANCE: provider})

    service.get_bars("TEST", Timeframe.DAY_1, DataSource.YFINANCE, limit=10)
    deep = service.get_bars("TEST", Timeframe.DAY_1, DataSource.YFINANCE, limit=400)

    assert len(deep) == 400
    assert provider.limits == [10, 400]


def test_a_shallower_request_after_a_deep_one_still_comes_from_cache():
    series = _bars([100.0 + i for i in range(500)], start=_START - timedelta(days=500))
    provider = _StubBarProvider(series)
    service = MarketDataService(providers={DataSource.YFINANCE: provider})

    service.get_bars("TEST", Timeframe.DAY_1, DataSource.YFINANCE, limit=400)
    service.get_bars("TEST", Timeframe.DAY_1, DataSource.YFINANCE, limit=10)

    assert provider.limits == [400]
