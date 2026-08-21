"""The strategy sandbox handed out the deployment's secrets.

FOUND BY AUDIT AND REPRODUCED HERE BEFORE ANY FIX WAS WRITTEN. Six lines, no
dunder anywhere, only an import from the allow-list:

    import collections
    class Strategy:
        def __init__(self):
            env = collections._sys.modules["os"].environ
            self.stolen = env.get("SECRET_ENCRYPTION_KEY", "")
        def on_tick(self, current_price) -> str:
            return "HOLD " + self.stolen

It ran, and it returned the value. CPython's collections/__init__.py contains
`import sys as _sys`, and `_sys` is ONE underscore -- the guard only rejected
dunders, so the whole chain walked straight through a module the allow-list
deliberately includes.

WHY IT IS THE WORST THING IN THE APP. POST /api/strategies/validate needs only
a login: it compiles the code, runs on_tick, and puts the return value in
`sample_signals` in the HTTP response. So the secrets come back in the response
body, without saving anything. On the deployed box that is
SECRET_ENCRYPTION_KEY (every stored broker credential, notification token and
backup passphrase), DATABASE_URL (direct read/write on the database) and
JWT_SECRET (a forged login token for any account).

The module's own header already said it was not a security boundary and that
the real answer is a separate process. That remains true and is not what this
file delivers. What it delivers is that the routes actually known to work are
closed, and that each of them stays closed -- one test per route, each of which
FAILED before the fix.
"""

import pytest

from app.services.strategy_runtime import StrategySecurityError, compile_strategy


def _strategy(body: str, tick: str = 'return "HOLD"') -> str:
    return f"""
class Strategy:
    def __init__(self):
        self.name = "probe"
        self.symbol = "AAPL"
{body}

    def on_tick(self, current_price) -> str:
        {tick}
"""


def _refused(source: str) -> None:
    with pytest.raises(StrategySecurityError):
        compile_strategy(source)


# --- the route that was actually exploited -------------------------------------------


def test_a_single_underscore_module_alias_cannot_be_used_to_reach_sys():
    """collections._sys is the exact payload that worked. One underscore, not
    two, so a dunder-only guard never looked at it."""
    _refused(
        "import collections\n"
        + _strategy('        self.stolen = collections._sys.modules["os"].environ')
    )


def test_the_same_route_through_any_other_allowed_module():
    """It was never specific to collections. Any module on the allow-list that
    happens to import another under a private alias is the same hole, and which
    ones do is a detail of whatever CPython version the deployment runs -- so
    the rule has to be about the SHAPE of the access, not about a list of known
    bad names."""
    for module in ("random", "functools", "statistics", "heapq", "bisect", "itertools"):
        _refused(
            f"import {module}\n" + _strategy(f'        self.stolen = {module}._sys.modules["os"]')
        )


def test_any_private_attribute_at_all_is_refused():
    """Nothing a strategy legitimately needs begins with an underscore. Price
    arithmetic uses public API."""
    _refused("import math\n" + _strategy("        self.x = math._pi_internal"))


# --- the routes that do not spell the attribute out ------------------------------------


def test_a_dunder_hidden_inside_a_format_string_is_refused():
    """str.format walks attributes named in the TEMPLATE, which is a plain
    string constant the AST never inspects:

        "{0._urandom.__self__.environ}".format(random)

    Same escape, spelled where the guard cannot see it.
    """
    _refused(
        "import random\n"
        + _strategy('        self.stolen = "{0._urandom.__self__}".format(random)')
    )


def test_format_map_and_f_string_style_traversal_too():
    _refused(
        "import random\n"
        + _strategy('        self.stolen = "{x.__class__}".format_map({"x": random})')
    )


def test_a_dunder_reached_through_a_computed_name_is_refused():
    """getattr was already forbidden. These are the other spellings."""
    _refused("import math\n" + _strategy('        self.x = math.__getattribute__("pi")'))


# --- and the things a real strategy does still work ------------------------------------


def test_an_ordinary_strategy_still_compiles():
    """The guard must not cost the feature. This is what the strategies the
    app ships actually look like."""
    source = """
import math
import statistics


class Strategy:
    def __init__(self):
        self.name = "moving average"
        self.symbol = "2330.TW"
        self.prices = []

    def on_tick(self, current_price) -> str:
        self.prices.append(current_price)
        if len(self.prices) < 5:
            return "HOLD"
        mean = statistics.mean(self.prices[-5:])
        spread = math.sqrt(abs(current_price - mean))
        return "BUY" if current_price > mean + spread else "HOLD"
"""

    loaded = compile_strategy(source)

    assert loaded.on_tick(100.0) in {"BUY", "HOLD", "SELL"}


def test_the_indicator_namespace_still_reaches_strategies():
    """The forty indicators are handed in as `indicators`, and they are the
    reason most strategies compile at all."""
    source = """
class Strategy:
    def __init__(self):
        self.name = "sma cross"
        self.symbol = "2330.TW"
        self.prices = []

    def on_tick(self, current_price) -> str:
        self.prices.append(current_price)
        if len(self.prices) < 25:
            return "HOLD"
        line = indicators.sma(self.prices, period=20)
        return "BUY" if line[-1] is not None and current_price > line[-1] else "HOLD"
"""

    loaded = compile_strategy(source)

    for price in range(100, 130):
        assert loaded.on_tick(float(price)) in {"BUY", "HOLD", "SELL"}


def test_a_leading_underscore_on_the_strategys_own_attribute_is_still_fine():
    """Refusing `self._prices` would break a convention people actually use,
    and self is the strategy's own object -- there is nothing to escape to."""
    source = _strategy("        self._prices = []", tick='return "HOLD"')

    assert compile_strategy(source) is not None


# --- the endpoint that returned the stolen value ----------------------------------------


def test_the_validate_endpoint_refuses_it_too(auth_client):
    """No saving required: /api/strategies/validate compiles, runs on_tick, and
    returns what it returned. That is how the secret came back over HTTP."""
    source = "import collections\n" + _strategy(
        '        self.stolen = str(collections._sys.modules["os"].environ)',
        tick="return self.stolen[:50]",
    )

    resp = auth_client.post("/api/strategies/validate", json={"source_code": source})

    assert resp.status_code in (200, 422)
    body = resp.text
    assert "PATH" not in body
    assert "SECRET" not in body
    if resp.status_code == 200:
        assert resp.json()["ok"] is False


# --- defence in depth: the object is not reachable, not merely unspellable ------------
#
# Everything above checks the STATIC pass, and a static pass only ever blocks
# the spellings somebody thought of. These check that the escape fails even
# when the guard is bypassed entirely: the module a strategy receives simply
# does not carry its private attributes.


def test_a_module_handed_to_a_strategy_has_no_private_attributes():
    from app.services.strategy_runtime import _guarded_import

    module = _guarded_import("collections")

    with pytest.raises(AttributeError):
        _ = module._sys


def test_the_public_api_of_that_module_still_works():
    from app.services.strategy_runtime import _guarded_import

    assert _guarded_import("collections").OrderedDict is not None
    assert _guarded_import("math").sqrt(16.0) == 4.0
    assert _guarded_import("statistics").mean([1.0, 2.0, 3.0]) == 2.0


def test_no_allowed_module_hands_out_another_module_at_runtime():
    """The real assertion. Not 「collections is patched」 but 「nothing on the
    allow-list exposes a private alias」 -- which is the shape of the bug, and
    which module happens to have one is a detail of the CPython version the
    deployment runs."""
    from app.services.strategy_runtime import _ALLOWED_MODULES, _guarded_import

    for name in sorted(_ALLOWED_MODULES):
        module = _guarded_import(name)
        leaked = [a for a in dir(module) if a.startswith("_") and not a.startswith("__")]
        assert not leaked, f"{name} still exposes {leaked}"


def test_the_reported_payload_returns_nothing_useful_end_to_end(monkeypatch):
    """The exact payload from the report, through the real compiler, with a
    marker in the environment. Before the fix this returned the marker."""
    import os

    monkeypatch.setitem(os.environ, "PROBE_SECRET", "THE-MARKER-THAT-MUST-NOT-ESCAPE")

    source = "import collections\n" + _strategy(
        "        self.stolen = collections._sys.modules['os'].environ",
        tick="return str(self.stolen)",
    )

    with pytest.raises(Exception) as caught:
        loaded = compile_strategy(source)
        assert "THE-MARKER-THAT-MUST-NOT-ESCAPE" not in loaded.on_tick(1.0)

    assert "THE-MARKER-THAT-MUST-NOT-ESCAPE" not in str(caught.value)
