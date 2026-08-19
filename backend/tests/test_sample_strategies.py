"""The samples are what someone copies before they know anything else.

Both shipped ones taught two things that are wrong for this app.

They ran on `on_tick`, which is fed a raw quote several times a minute, so a
"5-day moving average" was actually the average of the last five *ticks* --
and the strategy the owner ends up with fires on intraday noise rather than
on candles.

And the RSI one hand-rolled the calculation, using a simple mean where
`indicators.rsi` uses Wilder's smoothing, as every chart does. Following that
example produces a strategy whose RSI disagrees with the backtest's, with the
indicator catalogue's, and with TradingView's -- and nothing on screen would
ever explain the difference.
"""

import re
from pathlib import Path

import pytest

from app.services.indicators import registry
from app.services.strategy_runtime import compile_strategy

SAMPLES = Path(__file__).resolve().parent.parent / "app" / "strategies_storage" / "samples"


def _sources() -> list[tuple[str, str]]:
    return [(path.name, path.read_text(encoding="utf-8")) for path in sorted(SAMPLES.glob("*.py"))]


def test_there_are_samples_to_load():
    assert _sources(), "the 從範例載入 button needs something to load"


@pytest.mark.parametrize("name,source", _sources(), ids=lambda v: v if v.endswith(".py") else "")
def test_every_sample_compiles_in_the_sandbox(name, source):
    """A sample that does not load is worse than no sample: it reads as the
    app being broken."""
    loaded = compile_strategy(source)
    assert loaded.name
    assert loaded.symbol


@pytest.mark.parametrize("name,source", _sources(), ids=lambda v: v if v.endswith(".py") else "")
def test_every_sample_runs_on_candles_not_raw_quotes(name, source):
    """on_tick is fed a quote several times a minute, so "5-day moving
    average" written against it is really the average of five ticks. Anyone
    copying that gets a strategy firing on intraday noise."""
    loaded = compile_strategy(source)
    assert loaded.entry_point == "on_bar", f"{name} still runs on raw quotes"


@pytest.mark.parametrize("name,source", _sources(), ids=lambda v: v if v.endswith(".py") else "")
def test_no_sample_hand_rolls_an_indicator_the_app_already_provides(name, source):
    """Following a hand-rolled RSI produces a strategy whose numbers disagree
    with the backtest, the indicator catalogue and every chart -- and nothing
    on screen would ever explain why."""
    lowered = source.lower()
    for hint in ("def _rsi", "def _sma", "def _ema", "def _macd"):
        assert hint not in lowered, f"{name} reimplements something in indicators.*"


@pytest.mark.parametrize("name,source", _sources(), ids=lambda v: v if v.endswith(".py") else "")
def test_every_sample_declares_how_much_history_it_needs(name, source):
    """Without warmup_bars the strategy starts signalling off two candles and
    the first calls are noise."""
    assert "warmup_bars" in source, f"{name} does not say how much history it needs"


@pytest.mark.parametrize("name,source", _sources(), ids=lambda v: v if v.endswith(".py") else "")
def test_the_indicators_a_sample_calls_actually_exist(name, source):
    """A sample naming an indicator that was renamed would fail at the first
    tick, live, with the owner having changed nothing."""
    available = {entry.name for entry in registry.catalogue()}
    # A Python identifier and nothing else. Splitting on "(" instead swept up
    # the prose after a mention in a docstring, which is not a call.
    for called in re.findall(r"indicators\.([A-Za-z_][A-Za-z0-9_]*)", source):
        assert called in available, f"{name} calls indicators.{called}, which does not exist"


def test_a_sample_actually_produces_a_signal_on_data_that_should_trigger_it():
    """Compiling is not the bar. A sample that can only ever return HOLD looks
    identical to a working one until it has sat on screen for a week."""
    from decimal import Decimal

    from app.services.market_data.base import Bar, Timeframe

    source = (SAMPLES / "ma_cross.py").read_text(encoding="utf-8")
    loaded = compile_strategy(source)

    # A run down and then decisively up: a crossing has to happen somewhere.
    closes = [100.0] * 25 + [95.0] * 10 + list(range(96, 130))
    signals = set()
    for i, close in enumerate(closes):
        bar = Bar(
            symbol="TEST",
            timeframe=Timeframe.DAY_1,
            timestamp=None,
            open=float(close),
            high=float(close),
            low=float(close),
            close=float(close),
            volume=Decimal(1000),
        )
        object.__setattr__(bar, "timestamp", _at(i))
        signals.add(loaded.on_bar(bar))

    assert "BUY" in signals, "the sample never fires, which is not a sample"


def _at(index: int):
    from datetime import UTC, datetime, timedelta

    return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=index)
