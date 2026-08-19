import threading
from pathlib import Path

import pytest

from app.services.strategy_runtime import (
    LoadedStrategy,
    StrategyRegistry,
    StrategySecurityError,
    StrategyTimeoutError,
    StrategyValidationError,
    code_hash,
    compile_strategy,
)

_SAMPLES_DIR = Path(__file__).resolve().parent.parent / "app" / "strategies_storage" / "samples"

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


# --- sandboxing -------------------------------------------------------------


def _wrap_on_tick(body: str) -> str:
    """Builds a minimal valid Strategy whose on_tick body is `body` (already
    indented to 8 spaces by the caller's triple-quoted string)."""
    return f"""
class Strategy:
    def __init__(self):
        self.name = "probe"
        self.symbol = "AAPL"

    def on_tick(self, current_price: float) -> str:
{body}
        return "HOLD"
"""


def test_security_error_is_a_validation_error():
    # The strategies router only catches StrategyValidationError, so a rejected
    # sandbox violation has to arrive as a clean 422/ok=False, not a 500.
    assert issubclass(StrategySecurityError, StrategyValidationError)


@pytest.mark.parametrize("module", ["os", "sys", "subprocess", "socket", "httpx", "requests"])
def test_module_level_import_of_dangerous_module_is_rejected(module):
    with pytest.raises(StrategySecurityError):
        compile_strategy(f"import {module}\n" + MA5_SOURCE)


def test_import_hidden_inside_on_tick_is_rejected():
    # The exfiltration case: never runs at validate time, only later on a live
    # tick -- so it has to be caught statically, not by "did it raise once".
    source = _wrap_on_tick('        from os import environ\n        print(environ["JWT_SECRET"])')
    with pytest.raises(StrategySecurityError):
        compile_strategy(source)


def test_aliased_and_dotted_imports_are_rejected():
    with pytest.raises(StrategySecurityError):
        compile_strategy("import os.path as p\n" + MA5_SOURCE)


def test_opening_files_is_rejected():
    source = _wrap_on_tick('        open("/etc/passwd").read()')
    with pytest.raises(StrategySecurityError):
        compile_strategy(source)


def test_dunder_traversal_is_rejected():
    source = _wrap_on_tick("        print(().__class__.__base__.__subclasses__())")
    with pytest.raises(StrategySecurityError):
        compile_strategy(source)


@pytest.mark.parametrize(
    "body",
    [
        '        eval("1+1")',
        '        exec("x = 1")',
        '        getattr(self, "name")',
        '        __import__("os")',
        '        print(globals()["__builtins__"])',
    ],
)
def test_escape_hatch_builtins_are_rejected(body):
    with pytest.raises(StrategySecurityError):
        compile_strategy(_wrap_on_tick(body))


def test_dynamic_import_at_runtime_has_no_builtin_to_call():
    # Belt and braces: even if something slips past the AST scan, the exec
    # namespace itself must not hand out an unguarded __import__.
    loaded = compile_strategy(MA5_SOURCE)
    builtins_ns = loaded.instance.on_tick.__globals__["__builtins__"]
    assert "open" not in builtins_ns
    assert "eval" not in builtins_ns
    with pytest.raises(StrategySecurityError):
        builtins_ns["__import__"]("os")


def test_ordinary_maths_imports_still_work():
    source = """
import math
from statistics import mean

class Strategy:
    def __init__(self):
        self.name = "maths"
        self.symbol = "AAPL"
        self.prices = []

    def on_tick(self, current_price: float) -> str:
        self.prices.append(current_price)
        if math.sqrt(current_price) > mean(self.prices):
            return "BUY"
        return "HOLD"
"""
    loaded = compile_strategy(source)
    assert loaded.on_tick(100.0) == "HOLD"


@pytest.mark.parametrize("sample", ["ma5_cross.py", "rsi_threshold.py"])
def test_shipped_samples_still_compile_and_run_unchanged(sample):
    loaded = compile_strategy((_SAMPLES_DIR / sample).read_text())
    signals = [loaded.on_tick(100.0 + (i % 7) - 3) for i in range(40)]
    assert signals  # ran to completion under the sandbox
    assert set(signals) <= {"BUY", "SELL", "HOLD"}


# --- on_tick timeout --------------------------------------------------------


class _HangingStrategy:
    """Blocks until the test releases it -- stands in for `while True` or a
    network call that never returns."""

    def __init__(self, release: threading.Event) -> None:
        self.name = "hang"
        self.symbol = "AAPL"
        self.calls = 0
        self._release = release

    def on_tick(self, current_price: float) -> str:
        self.calls += 1
        self._release.wait(30)
        return "BUY"


def _loaded(instance: object, timeout_sec: float | None) -> LoadedStrategy:
    return LoadedStrategy(
        name="hang", symbol="AAPL", instance=instance, code_hash="x", timeout_sec=timeout_sec
    )


def test_timeout_error_is_a_validation_error():
    assert issubclass(StrategyTimeoutError, StrategyValidationError)


def test_on_tick_gives_up_instead_of_hanging_forever():
    release = threading.Event()
    loaded = _loaded(_HangingStrategy(release), 0.05)
    try:
        with pytest.raises(StrategyTimeoutError):
            loaded.on_tick(100.0)
    finally:
        release.set()


def test_a_still_running_tick_is_not_restarted_concurrently():
    release = threading.Event()
    instance = _HangingStrategy(release)
    loaded = _loaded(instance, 0.05)
    try:
        with pytest.raises(StrategyTimeoutError):
            loaded.on_tick(100.0)
        with pytest.raises(StrategyTimeoutError):
            loaded.on_tick(101.0)
        # The stuck call still owns the instance -- starting a second one would
        # race on self.prices and leak a thread per tick.
        assert instance.calls == 1
    finally:
        release.set()


def test_timeout_default_comes_from_settings(monkeypatch):
    monkeypatch.setattr("app.config.settings.STRATEGY_TICK_TIMEOUT_SEC", 0.05)
    release = threading.Event()
    loaded = _loaded(_HangingStrategy(release), None)
    try:
        with pytest.raises(StrategyTimeoutError):
            loaded.on_tick(100.0)
    finally:
        release.set()


def test_strategy_exceptions_still_propagate_unchanged():
    source = """
class Strategy:
    def __init__(self):
        self.name = "boom"
        self.symbol = "AAPL"

    def on_tick(self, current_price: float) -> str:
        raise RuntimeError("boom")
"""
    loaded = compile_strategy(source)
    with pytest.raises(RuntimeError, match="boom"):
        loaded.on_tick(100.0)
