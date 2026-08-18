"""Technical indicators, and the one catalogue that describes them.

Public surface:
    catalogue()            every registered indicator, for the API and the prompt
    get_indicator(name)    one of them by name
    indicator_namespace()  the `indicators` object strategy code sees

Importing this package is what fills the registry: each maths module registers
its own functions as it is imported, so there is nowhere for a hand-kept list
to live and go stale.
"""

import difflib
from types import MappingProxyType

from app.services.indicators import momentum, price, trend, volatility, volume
from app.services.indicators.registry import (
    INDICATOR_CATEGORIES,
    IndicatorCategory,
    IndicatorParam,
    IndicatorResult,
    IndicatorSpec,
    catalogue,
    get_indicator,
)

__all__ = [
    "INDICATOR_CATEGORIES",
    "IndicatorCategory",
    "IndicatorParam",
    "IndicatorResult",
    "IndicatorSpec",
    "catalogue",
    "get_indicator",
    "indicator_namespace",
    "momentum",
    "price",
    "trend",
    "volatility",
    "volume",
]


class _IndicatorNamespace:
    """The `indicators` object injected into the strategy sandbox.

    Deliberately not the module itself and not a SimpleNamespace: one instance
    is shared by every strategy in the process, so a strategy that could
    rebind `indicators.rsi` would rebind it for all of them -- and the
    replacement would go on returning plausible numbers. Read-only, and an
    unknown name says what the nearest real one is rather than the bare
    AttributeError a typo would otherwise produce at 03:00 on a live tick.

    This widens the sandbox by exactly one object holding plain functions.
    Their __globals__ would reach this module, but the AST scan already
    refuses every dunder attribute -- which is the same footing the rest of
    the sandbox stands on, and is re-checked in tests/test_indicators_sandbox.py.
    """

    def __init__(self, functions: dict) -> None:
        object.__setattr__(self, "_functions", MappingProxyType(dict(functions)))

    def __getattr__(self, name: str):
        try:
            return self._functions[name]
        except KeyError:
            suggestion = difflib.get_close_matches(name, self._functions, n=1)
            hint = f" Did you mean '{suggestion[0]}'?" if suggestion else ""
            raise AttributeError(f"There is no indicator called '{name}'.{hint}") from None

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(
            f"indicators.{name} cannot be reassigned -- the indicator library is shared "
            "by every strategy and is read-only."
        )

    def __delattr__(self, name: str) -> None:
        self.__setattr__(name, None)

    def __dir__(self) -> list[str]:
        return sorted(self._functions)


_NAMESPACE = _IndicatorNamespace({spec.name: spec.fn for spec in catalogue()})


def indicator_namespace() -> _IndicatorNamespace:
    """The single shared namespace handed to every compiled strategy."""
    return _NAMESPACE
