"""The catalogue must never advertise something that does not work.

A published list of indicators is only useful if it is the same list the
runtime actually has. Two ways for that to rot: the catalogue names something
that was never implemented (the owner writes a strategy against it and the
strategy fails at 03:00), or an indicator exists but was never registered (it
is invisible to the owner and to the AI, so it gets hand-rolled anyway). Both
are checked here, mechanically, against the real functions.
"""

import inspect

import pytest

from app.services.indicators import (
    INDICATOR_CATEGORIES,
    IndicatorCategory,
    IndicatorResult,
    catalogue,
    get_indicator,
    indicator_namespace,
    momentum,
    price,
    trend,
    volatility,
    volume,
)
from tests.indicator_data import CLOSES, HIGHS, LOWS, OPENS, VOLUMES

MATHS_MODULES = (trend, momentum, volatility, volume, price)

# Every series argument any registered indicator takes, by the exact parameter
# name it uses. A new indicator with an argument that is not in here fails
# test_every_registered_indicator_is_callable rather than going untested --
# which is the point: this map is not allowed to fall behind the registry.
SAMPLE_ARGUMENTS = {
    "values": CLOSES,
    "opens": OPENS,
    "highs": HIGHS,
    "lows": LOWS,
    "closes": CLOSES,
    "volumes": VOLUMES,
    # pivot_points takes one period's worth of scalars, not series.
    "high": 56.70,
    "low": 55.00,
    "close": 56.40,
}


def _call_arguments(spec, series=None):
    arguments = {}
    for param in spec.params:
        if param.name in SAMPLE_ARGUMENTS:
            supplied = SAMPLE_ARGUMENTS[param.name]
            if series is not None and isinstance(supplied, list):
                supplied = series
            arguments[param.name] = supplied
        elif param.required:
            raise AssertionError(
                f"{spec.name}() has a required argument '{param.name}' that "
                "tests/test_indicator_catalogue.py does not know how to supply"
            )
    return arguments


def test_the_catalogue_is_not_a_token_handful():
    """The owner asked for breadth on purpose: a missing indicator is one the
    AI hand-rolls, and a hand-rolled MACD is very easily subtly wrong."""
    assert len(catalogue()) >= 40
    by_category = {c: 0 for c in IndicatorCategory}
    for spec in catalogue():
        by_category[spec.category] += 1
    assert all(count >= 3 for count in by_category.values()), by_category


@pytest.mark.parametrize("spec", catalogue(), ids=lambda s: s.name)
def test_every_registered_indicator_is_callable(spec):
    result = spec.fn(**_call_arguments(spec))

    if spec.result is IndicatorResult.SERIES:
        assert isinstance(result, list)
        assert len(result) == len(CLOSES)
    elif spec.result is IndicatorResult.SERIES_MAP:
        assert set(result) == set(spec.keys)
        for key in spec.keys:
            assert isinstance(result[key], list), key
            assert len(result[key]) == len(CLOSES), key
    else:
        assert set(result) == set(spec.keys)
        assert all(isinstance(result[key], float) for key in spec.keys)


@pytest.mark.parametrize("spec", catalogue(), ids=lambda s: s.name)
def test_every_series_indicator_stays_aligned_with_however_much_history_it_gets(spec):
    """Alignment is the contract the whole library rests on: series[-1] is
    "now" and series[-2] is "the previous candle". An indicator that returns a
    different length for some input silently shifts every crossover test
    written against it. A one-bar or empty history is not hypothetical -- it
    is what a strategy sees on its very first tick."""
    if spec.result is IndicatorResult.VALUE_MAP:
        pytest.skip("pivot levels describe one period, not one bar each")

    for length in (0, 1, 2, 5):
        history = CLOSES[:length]
        result = spec.fn(**_call_arguments(spec, series=history))
        series = [result] if spec.result is IndicatorResult.SERIES else list(result.values())
        for values in series:
            assert len(values) == length, f"{spec.name} returned {len(values)} for {length} bars"


@pytest.mark.parametrize("spec", catalogue(), ids=lambda s: s.name)
def test_every_registered_indicator_has_a_traditional_chinese_description(spec):
    assert spec.title.strip()
    assert len(spec.description) >= 10
    # The owner does not read English. A description with no CJK characters in
    # it is a placeholder that got shipped.
    assert any("一" <= ch <= "鿿" for ch in spec.title)
    assert any("一" <= ch <= "鿿" for ch in spec.description)


@pytest.mark.parametrize("spec", catalogue(), ids=lambda s: s.name)
def test_declared_parameters_match_the_real_signature(spec):
    """The catalogue derives its parameter list from the function itself, so
    this is really a check that nothing has smuggled in a hand-written copy."""
    signature = inspect.signature(spec.fn)

    assert [p.name for p in spec.params] == list(signature.parameters)
    for declared, (_, actual) in zip(spec.params, signature.parameters.items(), strict=True):
        assert declared.required == (actual.default is inspect.Parameter.empty)
        if not declared.required:
            assert declared.default == actual.default


def test_every_public_function_in_the_maths_modules_is_registered():
    """The other rot direction: an indicator that exists but was never added
    to the catalogue is invisible to the owner and to the AI, so it gets
    hand-rolled anyway and the whole exercise is wasted."""
    registered = {spec.fn for spec in catalogue()}

    for module in MATHS_MODULES:
        for name, obj in vars(module).items():
            if name.startswith("_") or not inspect.isfunction(obj):
                continue
            if obj.__module__ != module.__name__:
                continue  # imported helper, not an indicator defined here
            assert obj in registered, f"{module.__name__}.{name} is not in the catalogue"


def test_names_are_unique_and_lookup_works():
    names = [spec.name for spec in catalogue()]

    assert len(names) == len(set(names))
    assert get_indicator("rsi").category is IndicatorCategory.MOMENTUM
    assert get_indicator("no_such_indicator") is None


def test_the_catalogue_is_ordered_by_category_then_name():
    """Stable ordering, so the API response and the prompt built from it do
    not reshuffle between restarts."""
    order = list(IndicatorCategory)
    keys = [(order.index(spec.category), spec.name) for spec in catalogue()]

    assert keys == sorted(keys)


def test_every_category_has_a_traditional_chinese_label():
    assert set(INDICATOR_CATEGORIES) == set(IndicatorCategory)
    for label in INDICATOR_CATEGORIES.values():
        assert any("一" <= ch <= "鿿" for ch in label)


def test_the_sandbox_namespace_is_exactly_the_catalogue():
    namespace = indicator_namespace()

    for spec in catalogue():
        assert getattr(namespace, spec.name) is spec.fn
    assert sorted(dir(namespace)) == sorted(spec.name for spec in catalogue())


def test_the_sandbox_namespace_refuses_unknown_names_helpfully():
    namespace = indicator_namespace()

    with pytest.raises(AttributeError, match="bollinger_bands"):
        namespace.bollinger  # noqa: B018 -- the attribute access IS the assertion


def test_the_sandbox_namespace_cannot_be_rewritten_by_a_strategy():
    """One shared namespace serves every strategy in the process. If a
    strategy could rebind `indicators.rsi`, it would rebind it for all of
    them -- and the corrupted one would still return plausible numbers."""
    namespace = indicator_namespace()

    with pytest.raises(AttributeError):
        namespace.rsi = lambda *a, **k: [1.0]
    assert indicator_namespace().rsi is momentum.rsi
