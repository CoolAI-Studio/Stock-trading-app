"""Tuning a strategy without editing Python.

CLAUDE.md says the audience is 「不會寫 Python 的使用者」 and that a price alert
they can set up without code is a CORE feature, not a bonus. But every number
inside a strategy -- the moving-average window, the threshold, how many bars to
look back -- is a literal in the source. Changing 5 to 20 means editing Python
in a textarea, which for this audience is the same as not being able to change
it at all.

WHAT IT LOOKS LIKE FROM THE STRATEGY'S SIDE. A dict of defaults, declared the
same way `timeframe` and `warmup_bars` already are:

    class Strategy:
        def __init__(self):
            self.name = "ma"
            self.symbol = "2330.TW"
            self.params = {"window": 5}

        def on_tick(self, price):
            window = self.params["window"]     # read HERE, not in __init__

THE CONTRACT, AND WHY. Overrides are merged into `self.params` AFTER __init__
has run, because that is the only injection point that does not change the
constructor signature every existing strategy already uses. So a value copied
out of `self.params` into another attribute during __init__ will not see the
override -- read the dict where the decision is made. Stated in the docs, in
the samples, and in the AI prompt, because a silently ignored setting is worse
than one that cannot be set.

ONLY DECLARED KEYS. An override for a key the strategy does not declare is
refused rather than merged: it means the source was edited and the stored
setting is now about something that no longer exists, and quietly keeping it
would leave a value in the database that the owner believes is doing something.
"""

import pytest

from app.services.strategy_runtime import StrategyValidationError, compile_strategy

TUNABLE = """
class Strategy:
    def __init__(self):
        self.name = "tunable"
        self.symbol = "2330.TW"
        self.params = {"window": 5, "threshold": 1.5, "enabled": True, "label": "hi"}
        self.prices = []

    def on_tick(self, current_price: float) -> str:
        self.prices.append(current_price)
        window = self.params["window"]
        if len(self.prices) < window:
            return "HOLD"
        return "BUY" if current_price > sum(self.prices[-window:]) / window else "HOLD"
"""

NO_PARAMS = """
class Strategy:
    def __init__(self):
        self.name = "plain"
        self.symbol = "AAPL"

    def on_tick(self, current_price: float) -> str:
        return "HOLD"
"""


# --- what the code declares ----------------------------------------------------


def test_the_declared_defaults_are_readable_without_running_the_strategy():
    """The form has to render a field per parameter, with the value the author
    chose already in it."""
    loaded = compile_strategy(TUNABLE)

    assert loaded.declared_params == {
        "window": 5,
        "threshold": 1.5,
        "enabled": True,
        "label": "hi",
    }


def test_a_strategy_with_no_parameters_declares_none():
    """Most strategies have none, and they must keep working untouched."""
    assert compile_strategy(NO_PARAMS).declared_params == {}


def test_a_params_attribute_that_is_not_a_dict_is_refused():
    """Caught at compile time, where the author can see it, rather than at the
    first tick in production."""
    source = TUNABLE.replace(
        'self.params = {"window": 5, "threshold": 1.5, "enabled": True, "label": "hi"}',
        "self.params = [1, 2, 3]",
    )

    with pytest.raises(StrategyValidationError) as excinfo:
        compile_strategy(source)

    assert "params" in str(excinfo.value)


def test_a_parameter_of_a_type_no_form_can_render_is_refused():
    """A dict or a list has no input box. Allowing it would produce a
    parameter that shows up in the API and can never be edited."""
    source = TUNABLE.replace('"window": 5', '"window": {"nested": 1}')

    with pytest.raises(StrategyValidationError):
        compile_strategy(source)


def test_a_non_string_key_is_refused():
    source = TUNABLE.replace('"window": 5', "5: 5")

    with pytest.raises(StrategyValidationError):
        compile_strategy(source)


# --- overriding them -------------------------------------------------------------


def test_an_override_reaches_the_running_instance():
    loaded = compile_strategy(TUNABLE, params={"window": 20})

    assert loaded.instance.params["window"] == 20


def test_the_others_keep_the_authors_defaults():
    """A partial override is the normal case: somebody changes one number."""
    loaded = compile_strategy(TUNABLE, params={"window": 20})

    assert loaded.instance.params["threshold"] == 1.5


def test_the_override_actually_changes_what_the_strategy_does():
    """The point. Everything above is bookkeeping if the decision does not
    move."""
    slow = compile_strategy(TUNABLE, params={"window": 4})
    for price in (10, 10, 10, 100):
        signal = slow.on_tick(float(price))

    assert signal == "BUY"


def test_declared_params_still_report_the_authors_defaults_not_the_overrides():
    """The form shows 「default 5, you set 20」. Reporting 20 as the default
    would lose the author's answer the moment somebody changed it."""
    loaded = compile_strategy(TUNABLE, params={"window": 20})

    assert loaded.declared_params["window"] == 5


def test_an_override_for_something_the_strategy_never_declared_is_refused():
    """It means the source was edited and this stored setting is now about
    something that no longer exists. Quietly dropping it leaves a value in the
    database the owner believes is doing something."""
    with pytest.raises(StrategyValidationError) as excinfo:
        compile_strategy(TUNABLE, params={"windwo": 20})

    assert "windwo" in str(excinfo.value)


def test_an_override_of_the_wrong_type_is_refused():
    """「20」 as text where a number was declared would compare as a string and
    silently never trigger."""
    with pytest.raises(StrategyValidationError):
        compile_strategy(TUNABLE, params={"window": "twenty"})


def test_a_whole_number_is_accepted_where_a_float_was_declared():
    """Typing 2 into a box whose default is 1.5 is not a mistake, and JSON has
    no way to say 「2.0」."""
    loaded = compile_strategy(TUNABLE, params={"threshold": 2})

    assert loaded.instance.params["threshold"] == 2


def test_a_bool_is_not_smuggled_in_as_a_number():
    """True == 1 in Python, so an unguarded int check accepts a checkbox value
    for a numeric field and vice versa."""
    with pytest.raises(StrategyValidationError):
        compile_strategy(TUNABLE, params={"window": True})


def test_overriding_nothing_is_the_same_as_not_overriding():
    assert compile_strategy(TUNABLE, params={}).instance.params["window"] == 5


def test_a_strategy_with_no_parameters_refuses_any_override():
    with pytest.raises(StrategyValidationError):
        compile_strategy(NO_PARAMS, params={"anything": 1})


# --- the cache has to notice ------------------------------------------------------


def test_changing_a_parameter_reloads_the_strategy():
    """The registry keys on the source hash. Without the parameters in that
    key, saving a new value would leave the old instance running -- the form
    would show 20 and the strategy would keep using 5, forever."""
    from app.services.strategy_runtime import StrategyRegistry

    registry = StrategyRegistry()
    first = registry.get_or_load(1, TUNABLE, params={"window": 5})
    second = registry.get_or_load(1, TUNABLE, params={"window": 20})

    assert first is not second
    assert second.instance.params["window"] == 20


def test_the_same_parameters_reuse_the_instance():
    """Reloading on every tick would throw away the accumulated price window
    the strategy needs to work at all."""
    from app.services.strategy_runtime import StrategyRegistry

    registry = StrategyRegistry()
    first = registry.get_or_load(1, TUNABLE, params={"window": 5})
    second = registry.get_or_load(1, TUNABLE, params={"window": 5})

    assert first is second


# --- stored, and actually used -----------------------------------------------


def _create(auth_client, **kw) -> dict:
    body = {"name": "tunable", "symbol": "2330.TW", "source_code": TUNABLE}
    body.update(kw)
    resp = auth_client.post("/api/strategies", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_the_validator_reports_what_the_code_declares(auth_client):
    """The form cannot render a field per parameter without being told what
    they are and what the author's defaults were."""
    body = auth_client.post("/api/strategies/validate", json={"source_code": TUNABLE}).json()

    assert body["declared_params"]["window"] == 5


def test_a_strategy_remembers_its_parameters(auth_client):
    created = _create(auth_client, params={"window": 20})

    assert auth_client.get(f"/api/strategies/{created['id']}").json()["params"] == {"window": 20}


def test_a_strategy_without_parameters_is_unaffected(auth_client):
    created = _create(auth_client, source_code=NO_PARAMS, symbol="AAPL")

    assert created["params"] == {}


def test_a_parameter_the_code_does_not_declare_is_refused_at_the_api(auth_client):
    resp = auth_client.post(
        "/api/strategies",
        json={"name": "x", "symbol": "2330.TW", "source_code": TUNABLE, "params": {"nope": 1}},
    )

    assert resp.status_code == 422, resp.text


def test_a_parameter_can_be_changed_without_touching_the_code(auth_client):
    """The whole point: somebody who cannot write Python changes the number."""
    created = _create(auth_client, params={"window": 20})

    resp = auth_client.patch(f"/api/strategies/{created['id']}", json={"params": {"window": 30}})

    assert resp.status_code == 200, resp.text
    assert resp.json()["params"] == {"window": 30}


def test_editing_a_parameter_drops_the_running_instance(auth_client, monkeypatch):
    """Otherwise the form says 30 and the strategy keeps deciding on 20 --
    silently, for as long as the process lives."""
    from app.services import market_loop

    created = _create(auth_client, params={"window": 20})
    market_loop._registry.get_or_load(created["id"], TUNABLE, params={"window": 20})
    assert market_loop._registry.is_cached(created["id"])

    auth_client.patch(f"/api/strategies/{created['id']}", json={"params": {"window": 30}})

    assert not market_loop._registry.is_cached(created["id"])


def test_the_worker_runs_the_stored_parameters(auth_client, db_session):
    """Stored and then ignored would be the worst of the three states."""
    from app.models.strategy import Strategy
    from app.services import market_loop

    created = _create(auth_client, params={"window": 20})
    strategy = db_session.get(Strategy, created["id"])

    loaded = market_loop._registry.get_or_load(
        strategy.id, strategy.source_code, params=strategy.params
    )

    # 隔著管線問，不伸手進 `.instance`：策略的實例住在子行程裡（#18），而那正是重
    # 點。問法換了，問的東西反而更接近使用者會遇到的事——不是「那個 dict 裡寫著
    # 20」，而是「它真的等了 20 個價才說話」。
    #
    # TUNABLE 在漲勢中滿了 window 個價就會 BUY。原始碼宣告的預設是 5：如果存起來
    # 的 20 被忽略，第五個價就會發訊號。
    signals = [loaded.on_tick(100.0 + step) for step in range(5)]

    assert signals == ["HOLD"] * 5, f"用的是原始碼的預設 5，不是存起來的 20：{signals}"
