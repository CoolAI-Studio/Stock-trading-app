"""The one in-code catalogue of every indicator.

Same pattern, and the same reasoning, as strategy_runtime.allowed_modules():
anything that needs to *state* what the library contains -- the API endpoint,
the AI system prompt, the sandbox namespace -- reads it from here rather than
keeping a copy. A retyped list goes stale the first time an indicator is
added, and then the catalogue advertises something that does not exist (the
owner writes a strategy against it and it fails on a live tick) or hides
something that does (the AI hand-rolls it instead, badly).

Parameters are not declared by hand either -- they are read off the function
signature at registration, so they cannot disagree with the real one.
"""

import inspect
import types
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class IndicatorCategory(StrEnum):
    TREND = "trend"
    MOMENTUM = "momentum"
    VOLATILITY = "volatility"
    VOLUME = "volume"
    PRICE = "price"


# Traditional Chinese, because the owner reads Traditional Chinese and this is
# the label the dashboard shows.
INDICATOR_CATEGORIES: MappingProxyType[IndicatorCategory, str] = MappingProxyType(
    {
        IndicatorCategory.TREND: "趨勢",
        IndicatorCategory.MOMENTUM: "動能",
        IndicatorCategory.VOLATILITY: "波動",
        IndicatorCategory.VOLUME: "成交量",
        IndicatorCategory.PRICE: "價格轉換",
    }
)


class IndicatorResult(StrEnum):
    """What shape comes back, so a caller knows whether to index it or key it."""

    # One list, the same length as the input series.
    SERIES = "series"
    # A dict of named lists, each the same length as the input series.
    SERIES_MAP = "series_map"
    # A dict of single numbers -- pivot levels, which describe one period
    # rather than one bar each.
    VALUE_MAP = "value_map"


@dataclass(frozen=True)
class IndicatorParam:
    name: str
    type: str
    required: bool
    default: float | int | str | bool | None


@dataclass(frozen=True)
class IndicatorSpec:
    name: str
    category: IndicatorCategory
    title: str
    description: str
    result: IndicatorResult
    keys: tuple[str, ...]
    params: tuple[IndicatorParam, ...]
    fn: Callable

    def signature(self) -> str:
        """How the indicator is spelled in strategy code, e.g.
        `rsi(values, period=14)`. Rendered from the registered parameters so
        the AI prompt and the API cannot describe a call that would fail."""
        arguments = [
            param.name if param.required else f"{param.name}={param.default!r}"
            for param in self.params
        ]
        return f"{self.name}({', '.join(arguments)})"

    def returns(self) -> str:
        """One phrase for the shape that comes back."""
        if self.result is IndicatorResult.SERIES:
            return "series"
        shape = ", ".join(self.keys)
        suffix = " (numbers, not series)" if self.result is IndicatorResult.VALUE_MAP else ""
        return f"dict with {shape}{suffix}"


_REGISTRY: dict[str, IndicatorSpec] = {}

# Defaults have to survive a trip through JSON to reach the browser, so a
# mutable or exotic default would be a registration-time bug, not a runtime
# surprise.
_JSON_SAFE = (int, float, str, bool, type(None))


def _annotation_name(annotation: object) -> str:
    """A short, honest type name for the catalogue.

    `list[float].__name__` is just "list", which would tell the reader nothing
    about whether an argument wants a series or a single number -- the one
    distinction that matters when calling these. Generic and union aliases are
    therefore rendered with str(), which keeps the parameters.
    """
    if annotation is inspect.Parameter.empty:
        return "unknown"
    if isinstance(annotation, str):
        return annotation
    if isinstance(annotation, types.GenericAlias | types.UnionType):
        return str(annotation)
    return getattr(annotation, "__name__", None) or str(annotation).replace("typing.", "")


def _params_of(fn: Callable) -> tuple[IndicatorParam, ...]:
    params = []
    for name, parameter in inspect.signature(fn).parameters.items():
        required = parameter.default is inspect.Parameter.empty
        default = None if required else parameter.default
        if not isinstance(default, _JSON_SAFE):
            raise TypeError(f"{fn.__name__}({name}=) default {default!r} is not JSON-safe.")
        params.append(
            IndicatorParam(
                name=name,
                type=_annotation_name(parameter.annotation),
                required=required,
                default=default,
            )
        )
    return tuple(params)


def indicator(
    *,
    category: IndicatorCategory,
    title: str,
    description: str,
    result: IndicatorResult = IndicatorResult.SERIES,
    keys: tuple[str, ...] = (),
):
    """Register an indicator under its own function name.

    The decorator returns the function untouched: there is no wrapper between
    the catalogue entry and the thing a strategy calls, so what is advertised
    and what runs cannot be two different objects.
    """

    def register(fn: Callable) -> Callable:
        if fn.__name__ in _REGISTRY:
            raise ValueError(f"Two indicators are registered as '{fn.__name__}'.")
        if (result is IndicatorResult.SERIES) != (not keys):
            raise ValueError(f"{fn.__name__}: only a map result has keys, and it must have some.")
        _REGISTRY[fn.__name__] = IndicatorSpec(
            name=fn.__name__,
            category=category,
            title=title,
            description=description,
            result=result,
            keys=tuple(keys),
            params=_params_of(fn),
            fn=fn,
        )
        return fn

    return register


_CATEGORY_ORDER = {category: index for index, category in enumerate(IndicatorCategory)}


def catalogue(category: IndicatorCategory | None = None) -> list[IndicatorSpec]:
    """Every registered indicator, category order then name.

    Sorted rather than insertion-ordered so the API response and the prompt
    built from it stay byte-identical across restarts and across whatever
    order the maths modules happen to import in.
    """
    specs = sorted(_REGISTRY.values(), key=lambda spec: (_CATEGORY_ORDER[spec.category], spec.name))
    return [spec for spec in specs if category is None or spec.category is category]


def get_indicator(name: str) -> IndicatorSpec | None:
    return _REGISTRY.get(name)
