"""The on_bar entry point: a strategy that thinks in candles rather than in
5-second price ticks.

on_tick is unchanged and stays the default; everything here is about the
second, optional shape and the validator being explicit about which one a
given piece of source actually uses.
"""

import threading
from datetime import UTC, datetime, timedelta

import pytest

from app.services.market_data.base import Bar, Timeframe
from app.services.strategy_runtime import (
    LoadedStrategy,
    StrategyTimeoutError,
    StrategyValidationError,
    compile_strategy,
)

WEEKLY_SOURCE = """
class Strategy:
    def __init__(self):
        self.name = "TSMC_weekly"
        self.symbol = "2330.TW"
        self.timeframe = "1wk"
        self.warmup_bars = 35
        self.closes = []

    def on_bar(self, bar) -> str:
        self.closes.append(bar.close)
        return "HOLD"
"""

TICK_SOURCE = """
class Strategy:
    def __init__(self):
        self.name = "plain_tick"
        self.symbol = "AAPL"

    def on_tick(self, current_price: float) -> str:
        return "HOLD"
"""


def _bars(closes: list[float], timeframe=Timeframe.WEEK_1) -> list[Bar]:
    start = datetime(2026, 1, 5, tzinfo=UTC)
    return [
        Bar(
            symbol="2330.TW",
            timeframe=timeframe,
            timestamp=start + timedelta(weeks=i),
            open=close,
            high=close + 1,
            low=close - 1,
            close=close,
            volume=1000.0 + i,
        )
        for i, close in enumerate(closes)
    ]


# --- which entry point did we find ------------------------------------------


def test_an_on_bar_strategy_is_accepted_and_named_as_such():
    loaded = compile_strategy(WEEKLY_SOURCE)

    assert loaded.entry_point == "on_bar"
    assert loaded.timeframe is Timeframe.WEEK_1
    assert loaded.warmup_bars == 35


def test_an_on_tick_strategy_still_reports_on_tick():
    loaded = compile_strategy(TICK_SOURCE)

    assert loaded.entry_point == "on_tick"
    # Irrelevant to a tick strategy, but it must still be a real timeframe
    # rather than None so nothing downstream has to special-case it.
    assert loaded.timeframe is Timeframe.DAY_1
    assert loaded.warmup_bars is None


def test_a_strategy_with_neither_entry_point_is_rejected_by_name():
    source = """
class Strategy:
    def __init__(self):
        self.name = "nothing"
        self.symbol = "AAPL"
"""
    with pytest.raises(StrategyValidationError) as exc_info:
        compile_strategy(source)

    message = str(exc_info.value)
    assert "on_tick" in message
    assert "on_bar" in message


def test_a_strategy_that_defines_both_entry_points_is_rejected():
    """Ambiguity here is the exact failure this work exists to stop: whichever
    one the runtime picked, the other half of the code would look live and be
    dead."""
    source = """
class Strategy:
    def __init__(self):
        self.name = "both"
        self.symbol = "AAPL"

    def on_tick(self, current_price: float) -> str:
        return "HOLD"

    def on_bar(self, bar) -> str:
        return "HOLD"
"""
    with pytest.raises(StrategyValidationError, match="both"):
        compile_strategy(source)


def test_a_timeframe_that_is_not_a_real_candle_size_is_rejected():
    source = WEEKLY_SOURCE.replace('"1wk"', '"weekly"')

    with pytest.raises(StrategyValidationError) as exc_info:
        compile_strategy(source)

    message = str(exc_info.value)
    assert "weekly" in message
    assert "1wk" in message  # the error has to say what to write instead


def test_a_bar_strategy_without_a_declared_timeframe_gets_the_default():
    source = WEEKLY_SOURCE.replace('        self.timeframe = "1wk"\n', "")

    assert compile_strategy(source).timeframe is Timeframe.DAY_1


@pytest.mark.parametrize("declared", ["-1", '"lots"'])
def test_a_nonsense_warmup_declaration_is_rejected(declared):
    source = WEEKLY_SOURCE.replace("self.warmup_bars = 35", f"self.warmup_bars = {declared}")

    with pytest.raises(StrategyValidationError, match="warmup_bars"):
        compile_strategy(source)


# --- the candle reaches the sandbox intact ----------------------------------


def test_a_strategy_can_read_every_field_off_the_candle():
    source = """
class Strategy:
    def __init__(self):
        self.name = "ohlc"
        self.symbol = "2330.TW"
        self.timeframe = "1wk"
        self.seen = []

    def on_bar(self, bar) -> str:
        self.seen.append((bar.open, bar.high, bar.low, bar.close, bar.volume, bar.timestamp))
        if bar.high > bar.low and bar.close > 0:
            return "BUY"
        return "HOLD"
"""
    loaded = compile_strategy(source)
    bar = _bars([100.0])[0]

    assert loaded.on_bar(bar) == "BUY"
    assert loaded.instance.seen[0][:5] == (100.0, 101.0, 99.0, 100.0, 1000.0)


def test_warm_up_replays_the_history_without_reporting_signals():
    """Replay exists to fill the indicator's memory, not to trade: every
    candle in it closed before the owner switched the strategy on."""
    loaded = compile_strategy(WEEKLY_SOURCE)

    assert loaded.warm_up(_bars([100.0, 101.0, 102.0])) is None
    assert loaded.instance.closes == [100.0, 101.0, 102.0]


# --- what the owner actually asked for --------------------------------------


def test_counting_candles_after_a_trigger_is_expressible():
    """The shape of the owner's request -- "after <condition>, wait for the
    SECOND candle and decide at its close". With only on_tick(price) this
    could not be written at all: a 5-second tick is not a candle, so
    "the second candle after" had no meaning to count in.
    """
    source = """
class Strategy:
    def __init__(self):
        self.name = "second_candle_after_trigger"
        self.symbol = "2330.TW"
        self.timeframe = "1wk"
        self.warmup_bars = 2
        self.closes = []
        self.bars_since_trigger = None
        self.trigger_close = 0.0

    def on_bar(self, bar) -> str:
        self.closes.append(bar.close)

        # 觸發後只數K棒，數到第二根收盤才判斷
        if self.bars_since_trigger is not None:
            self.bars_since_trigger += 1
            if self.bars_since_trigger == 2:
                self.bars_since_trigger = None
                if bar.close < self.trigger_close:
                    return "SELL"
            return "HOLD"

        if len(self.closes) >= 2 and bar.close < self.closes[-2]:
            self.bars_since_trigger = 0
            self.trigger_close = bar.close
        return "HOLD"
"""
    loaded = compile_strategy(source)

    # 100 101 102 then a down candle (the trigger), one candle of waiting,
    # and the decision lands on the second one after it.
    signals = [loaded.on_bar(bar) for bar in _bars([100.0, 101.0, 102.0, 99.0, 98.0, 97.0])]

    assert signals == ["HOLD", "HOLD", "HOLD", "HOLD", "HOLD", "SELL"]


# --- the wall-clock deadline covers on_bar too -------------------------------


class _HangingBarStrategy:
    def __init__(self, release: threading.Event) -> None:
        self.name = "hang"
        self.symbol = "AAPL"
        self.calls = 0
        self._release = release

    def on_bar(self, bar) -> str:
        self.calls += 1
        self._release.wait(30)
        return "BUY"


def test_on_bar_gives_up_instead_of_hanging_forever():
    release = threading.Event()
    instance = _HangingBarStrategy(release)
    loaded = LoadedStrategy(
        name="hang",
        symbol="AAPL",
        instance=instance,
        code_hash="x",
        timeout_sec=0.05,
        entry_point="on_bar",
    )
    try:
        with pytest.raises(StrategyTimeoutError):
            loaded.on_bar(_bars([100.0])[0])
        # Same rule as on_tick: the stuck call keeps the instance, so the next
        # poll is refused rather than started alongside it.
        with pytest.raises(StrategyTimeoutError):
            loaded.on_bar(_bars([101.0])[0])
        assert instance.calls == 1
    finally:
        release.set()


def test_warm_up_gives_up_instead_of_hanging_forever():
    release = threading.Event()
    loaded = LoadedStrategy(
        name="hang",
        symbol="AAPL",
        instance=_HangingBarStrategy(release),
        code_hash="x",
        timeout_sec=0.05,
        entry_point="on_bar",
    )
    try:
        with pytest.raises(StrategyTimeoutError):
            loaded.warm_up(_bars([100.0, 101.0]))
    finally:
        release.set()
