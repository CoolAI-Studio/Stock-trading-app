"""Indicators reaching strategy code -- without widening the sandbox.

The sandbox exists to stop pasted or AI-written strategy code from reaching
os.environ, the filesystem and the network. Handing that code a new object is
exactly the kind of change that quietly reopens it, so the escape routes that
were closed before are re-checked here THROUGH the new object.
"""

import pytest

from app.api.routers.strategies import _SAMPLES_DIR
from app.services.indicators import catalogue, momentum
from app.services.market_data.base import Timeframe, bars_from_closes
from app.services.strategy_generator import build_system_prompt
from app.services.strategy_runtime import (
    StrategySecurityError,
    StrategyValidationError,
    compile_strategy,
)
from tests.indicator_data import CLOSES


def _rally_then_decline() -> list[float]:
    """A rally with small pullbacks (RSI peaks above 95) and then a decline,
    so a strategy gated on "RSI > 80 first" actually reaches its exit branch
    rather than holding for want of a trigger."""
    closes, price = [], 100.0
    for i in range(45):
        price += 2.2 if i % 5 else -0.6
        closes.append(round(price, 2))
    for i in range(20):
        price -= 1.9 if i % 4 else -0.5
        closes.append(round(price, 2))
    return closes


def _tick_strategy(body: str) -> str:
    return f"""
class Strategy:
    def __init__(self):
        self.name = "indicator_user"
        self.symbol = "2330.TW"
        self.closes = []

    def on_tick(self, current_price: float) -> str:
{body}
"""


def test_a_strategy_can_call_an_indicator_without_importing_anything():
    source = _tick_strategy(
        """        self.closes.append(current_price)
        rsi = indicators.rsi(self.closes, 14)
        if rsi[-1] is None:
            return "HOLD"
        return "SELL" if rsi[-1] > 70 else "HOLD\""""
    )

    loaded = compile_strategy(source)
    signals = [loaded.on_tick(price) for price in CLOSES]

    assert signals[13] == "HOLD"  # still warming up
    assert "SELL" in signals


def test_the_indicator_a_strategy_gets_is_the_catalogued_one():
    """Not a copy, not a wrapper that rounds -- the same function the
    catalogue advertises and the tests verify."""
    source = _tick_strategy(
        """        self.closes.append(current_price)
        self.last = indicators.rsi(self.closes, 14)[-1]
        return "HOLD\""""
    )

    loaded = compile_strategy(source)
    for price in CLOSES:
        loaded.on_tick(price)

    assert loaded.instance.last == momentum.rsi(CLOSES, 14)[-1]
    assert loaded.instance.last is not None


def test_the_owners_own_strategy_shape_is_now_writable():
    """RSI over 80, then the MACD line crossing below its signal, then a
    decision on the close of the SECOND candle after that cross. Every piece
    of that sentence needed something this runtime did not have; the only
    remaining gap was correct indicators."""
    source = """
class Strategy:
    def __init__(self):
        self.name = "TSMC_weekly_rsi_macd"
        self.symbol = "2330.TW"
        self.timeframe = "1wk"
        self.warmup_bars = 40
        self.closes = []
        self.rsi_was_hot = False
        self.bars_since_cross = None

    def on_bar(self, bar) -> str:
        self.closes.append(bar.close)
        if len(self.closes) < 36:
            return "HOLD"

        rsi = indicators.rsi(self.closes, 14)
        macd = indicators.macd(self.closes)
        line, signal = macd["macd"], macd["signal"]
        if rsi[-1] is None or signal[-2] is None:
            return "HOLD"

        if rsi[-1] > 80:
            self.rsi_was_hot = True

        crossed_down = line[-2] >= signal[-2] and line[-1] < signal[-1]
        if self.rsi_was_hot and crossed_down:
            self.bars_since_cross = 0
            return "HOLD"

        if self.bars_since_cross is not None:
            self.bars_since_cross += 1
            if self.bars_since_cross == 2:
                self.bars_since_cross = None
                # 快慢線還沒收斂就出場
                if abs(line[-1] - signal[-1]) > abs(line[-3] - signal[-3]):
                    return "SELL"
        return "HOLD"
"""
    loaded = compile_strategy(source)
    bars = bars_from_closes("2330.TW", Timeframe.WEEK_1, _rally_then_decline())

    assert loaded.entry_point == "on_bar"
    assert loaded.timeframe is Timeframe.WEEK_1
    # The whole point is that it RUNS and fires -- "the second candle after
    # the cross" is a sentence the old on_tick(price) runtime could not
    # express at all, and an indicator library it cannot reach is no better.
    signals = [loaded.on_bar(bar) for bar in bars]
    assert set(signals) <= {"BUY", "SELL", "HOLD"}
    assert loaded.instance.rsi_was_hot is True
    assert [i for i, signal in enumerate(signals) if signal == "SELL"] == [37, 42, 48]


def test_indicators_cannot_be_used_to_reach_the_module_globals():
    """A plain function carries its module's globals on __globals__. The AST
    scan already refuses every dunder attribute, so this route is closed --
    but it is closed here explicitly, because `indicators` is the first
    Python-level object the sandbox hands out."""
    source = _tick_strategy(
        '        return indicators.rsi.__globals__["__builtins__"]["open"]("/etc/passwd")'
    )

    with pytest.raises(StrategySecurityError, match="__globals__"):
        compile_strategy(source)


def test_the_import_allowlist_did_not_grow():
    source = _tick_strategy('        import os\n        return "HOLD"')

    with pytest.raises(StrategySecurityError, match="os"):
        compile_strategy(source)


def test_indicators_is_not_importable_only_injected():
    """Adding it to the import allowlist would have meant teaching the
    guarded __import__ to resolve a name that is not a real top-level module.
    It is a plain global instead, so the allowlist is untouched."""
    source = _tick_strategy('        import indicators\n        return "HOLD"')

    with pytest.raises(StrategySecurityError):
        compile_strategy(source)


def test_a_strategy_that_misuses_an_indicator_fails_as_an_ordinary_error():
    """Bad arguments must surface as a normal strategy error -- caught by the
    market loop's consecutive-error guard -- not as a security error, which
    the router turns into a different message entirely."""
    source = _tick_strategy(
        """        self.closes.append(current_price)
        indicators.sma(self.closes, 0)
        return "HOLD\""""
    )

    loaded = compile_strategy(source)
    with pytest.raises(ValueError, match="period"):
        loaded.on_tick(100.0)


def test_two_strategies_share_the_namespace_without_sharing_state():
    first = compile_strategy(
        _tick_strategy(
            """        self.closes.append(current_price)
        self.value = indicators.sma(self.closes, 3)[-1]
        return "HOLD\""""
        )
    )
    second = compile_strategy(
        _tick_strategy(
            """        self.closes.append(current_price)
        self.value = indicators.sma(self.closes, 3)[-1]
        return "HOLD\""""
        )
    )

    for price in (10.0, 20.0, 30.0):
        first.on_tick(price)
    second.on_tick(99.0)

    assert first.instance.value == pytest.approx(20.0)
    assert second.instance.value is None


def test_a_strategy_cannot_replace_an_indicator_for_everyone_else():
    source = _tick_strategy(
        """        indicators.rsi = lambda *a: [100.0]
        return "HOLD\""""
    )

    loaded = compile_strategy(source)
    with pytest.raises(AttributeError):
        loaded.on_tick(100.0)


def test_existing_tick_strategies_still_compile_untouched():
    """on_tick is not going away and the shipped samples must keep working
    exactly as they did before indicators existed."""
    sources = [path.read_text() for path in sorted(_SAMPLES_DIR.glob("*.py"))]
    assert sources

    for source in sources:
        loaded = compile_strategy(source)
        assert loaded.entry_point == "on_tick"
        assert [loaded.on_tick(price) for price in CLOSES]


# --- the AI has to be told the library exists --------------------------------


def test_the_generator_prompt_lists_the_catalogue_rather_than_a_retyped_copy():
    """Same reasoning as allowed_modules(): a hand-maintained list in the
    prompt goes stale the moment an indicator is added, and the model then
    writes code against an indicator that is not there -- or, worse, quietly
    hand-rolls one that is."""
    prompt = build_system_prompt()

    for spec in catalogue():
        assert spec.name in prompt, spec.name
    assert "indicators.rsi" in prompt
    # ...and it must warn the model off the thing it does by default. Compared
    # with the wrapping collapsed, so re-flowing a paragraph is not a failure.
    assert "do not re-implement" in " ".join(prompt.lower().split())


def test_the_generator_prompt_still_states_the_sandbox_rules():
    prompt = build_system_prompt()

    assert "numpy" in prompt  # named as unavailable
    assert "__import__" in prompt


def test_indicator_misuse_inside_generated_code_is_still_a_validation_error():
    source = _tick_strategy("        return indicators.no_such_thing(self.closes)")

    loaded = compile_strategy(source)
    with pytest.raises(AttributeError):
        loaded.on_tick(100.0)
    # ...and compiling something that never had a Strategy class is unchanged.
    with pytest.raises(StrategyValidationError):
        compile_strategy("x = indicators.rsi([1.0], 2)")
