import pytest

from app.services.strategy_runtime import (
    StrategyRegistry,
    StrategyValidationError,
    code_hash,
    compile_strategy,
)

MA5_SOURCE = """
class Strategy:
    def __init__(self):
        self.name = "AAPL_MA5_Trend"
        self.symbol = "AAPL"
        self.prices = []

    def on_tick(self, current_price: float) -> str:
        self.prices.append(current_price)
        if len(self.prices) > 20:
            self.prices.pop(0)
        if len(self.prices) < 5:
            return "HOLD"
        ma5 = sum(self.prices[-5:]) / 5
        prev_price = self.prices[-2]
        if prev_price < ma5 and current_price > ma5:
            return "BUY"
        elif prev_price > ma5 and current_price < ma5:
            return "SELL"
        return "HOLD"
"""


def test_compile_strategy_extracts_name_and_symbol():
    loaded = compile_strategy(MA5_SOURCE)
    assert loaded.name == "AAPL_MA5_Trend"
    assert loaded.symbol == "AAPL"
    assert loaded.on_tick(100.0) == "HOLD"


def test_compile_strategy_rejects_syntax_error():
    with pytest.raises(StrategyValidationError):
        compile_strategy("def broken(:\n    pass")


def test_compile_strategy_rejects_missing_strategy_class():
    with pytest.raises(StrategyValidationError):
        compile_strategy("x = 1")


def test_compile_strategy_rejects_missing_on_tick():
    source = """
class Strategy:
    def __init__(self):
        self.name = "n"
        self.symbol = "AAPL"
"""
    with pytest.raises(StrategyValidationError):
        compile_strategy(source)


def test_compile_strategy_rejects_instantiation_error():
    source = """
class Strategy:
    def __init__(self):
        raise RuntimeError("boom")
"""
    with pytest.raises(StrategyValidationError):
        compile_strategy(source)


def test_registry_reuses_instance_state_across_calls():
    registry = StrategyRegistry()
    first = registry.get_or_load(1, MA5_SOURCE)
    first.on_tick(100.0)
    first.on_tick(101.0)

    second = registry.get_or_load(1, MA5_SOURCE)
    assert second is first  # same object -> accumulated self.prices survives


def test_registry_invalidates_on_code_change():
    registry = StrategyRegistry()
    first = registry.get_or_load(1, MA5_SOURCE)

    changed_source = MA5_SOURCE.replace('self.symbol = "AAPL"', 'self.symbol = "TSLA"')
    second = registry.get_or_load(1, changed_source)

    assert second is not first
    assert second.symbol == "TSLA"


def test_code_hash_is_stable_and_content_sensitive():
    assert code_hash(MA5_SOURCE) == code_hash(MA5_SOURCE)
    assert code_hash(MA5_SOURCE) != code_hash(MA5_SOURCE + "\n# comment")
