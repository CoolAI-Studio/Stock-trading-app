import ast
import builtins
import hashlib
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from app.config import settings
from app.services.indicators import indicator_namespace
from app.services.market_data.base import DEFAULT_TIMEFRAME, Bar, Timeframe


class StrategyValidationError(Exception):
    pass


class StrategySecurityError(StrategyValidationError):
    """Strategy source reached for something the sandbox denies.

    Subclasses StrategyValidationError so the strategies router turns it into
    the same clean 422 it already gives malformed code."""


class StrategyTimeoutError(StrategyValidationError):
    """on_tick() blew its wall-clock budget. Shares the same base so every
    failure the strategy subsystem can produce is catchable in one place."""


# --- sandbox ----------------------------------------------------------------
#
# HONEST SCOPE: this is a guard-rail against footguns and AI-generated slop --
# a pasted "strategy" that helpfully phones home, or an LLM that reached for
# `requests` because the training data did. It is NOT a security boundary. A
# determined attacker WILL escape any Python-level restriction (the CPython
# object graph is full of paths back to arbitrary code, and both the AST scan
# and the builtins table are denial by enumeration). Only a separate process
# with dropped privileges, or a container/WASM runtime, actually contains
# hostile code. Until strategies come from someone other than the account
# owner, that isolation isn't worth its operational cost -- but nothing here
# should be mistaken for it.
#
# What it does buy: on the production host os.environ holds
# SECRET_ENCRYPTION_KEY, DATABASE_URL and JWT_SECRET, and the obvious way to
# reach them -- `import os` -- now fails loudly at save time instead of
# silently at 03:00 on a live tick.

# Import allowlist rather than a denylist of os/sys/subprocess/socket/httpx/
# requests: a denylist is one stdlib module away from being wrong (pathlib,
# shutil, ctypes, importlib, webbrowser...). Strategies are arithmetic over a
# price series, so the useful surface is small. Extend deliberately.
_ALLOWED_MODULES = frozenset(
    {
        "math",
        "statistics",
        "decimal",
        "fractions",
        "random",
        "datetime",
        "collections",
        "itertools",
        "functools",
        "heapq",
        "bisect",
    }
)

# Everything that turns a string into code, reaches the interpreter's own
# state, or touches the filesystem. `getattr`/`setattr`/`vars` are in here
# because they are how you spell dunder traversal without writing a dunder.
_FORBIDDEN_NAMES = frozenset(
    {
        "__import__",
        "breakpoint",
        "compile",
        "delattr",
        "eval",
        "exec",
        "exit",
        "getattr",
        "globals",
        "help",
        "input",
        "locals",
        "memoryview",
        "open",
        "quit",
        "setattr",
        "vars",
    }
)

_SAFE_BUILTIN_NAMES = frozenset(
    {
        "abs", "all", "any", "bool", "callable", "chr", "classmethod", "dict", "divmod",
        # `format` is NOT here, deliberately. str.format walks attributes named
        # in the TEMPLATE, and the template is a plain string constant that the
        # AST guard cannot look inside:
        #     "{0._urandom.__self__.environ}".format(random)
        # is the same escape as a dunder chain, spelled where no static pass
        # will ever see it. f-strings and %-formatting cover everything a
        # strategy needs to build a signal string.
        "enumerate", "filter", "float", "frozenset", "hasattr", "hash", "int",
        "isinstance", "issubclass", "iter", "len", "list", "map", "max", "min", "next",
        "object", "ord", "pow", "print", "property", "range", "repr", "reversed", "round",
        "set", "slice", "sorted", "staticmethod", "str", "sum", "super", "tuple", "type",
        "zip",
        # Exceptions, so a strategy can do its own error handling.
        "ArithmeticError", "AttributeError", "Exception", "IndexError", "KeyError",
        "OverflowError", "RuntimeError", "StopIteration", "TypeError", "ValueError",
        "ZeroDivisionError",
    }
)  # fmt: skip


def allowed_modules() -> list[str]:
    """Read-only view of the import allowlist for code that needs to *state*
    the sandbox rules rather than enforce them -- the AI strategy generator
    puts this list in its system prompt. A retyped copy would go stale the
    moment the sandbox is widened or narrowed, and the model would then
    cheerfully generate code that save-time validation rejects."""
    return sorted(_ALLOWED_MODULES)


def forbidden_names() -> list[str]:
    """Companion to allowed_modules(); same reasoning."""
    return sorted(_FORBIDDEN_NAMES)


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


class _PublicModule:
    """A module with its private attributes removed.

    THE SECOND LAYER, and the one that does not depend on foreseeing a
    spelling. The static pass refuses `collections._sys` in source; this makes
    the attribute absent, so a route nobody thought of finds nothing there
    either.

    It exists because of the shape of the bug that was actually exploited:
    CPython's collections/__init__.py does `import sys as _sys`, so a module
    the allow-list deliberately includes was handing out the real `sys`, and
    from there `sys.modules["os"].environ` is the whole environment --
    SECRET_ENCRYPTION_KEY, DATABASE_URL, JWT_SECRET. Which standard-library
    module happens to keep a private alias is a detail of the interpreter
    version, not something to enumerate.

    Public API is passed straight through, so `math.sqrt`, `statistics.mean`
    and `collections.OrderedDict` are untouched.

    NOT A SANDBOX. Strategy code still runs in this process, and the module
    header above says what the real answer is: a separate process with a
    cleared environment and its own limits. This narrows the hole that was
    open; it does not turn the sandbox into a boundary.
    """

    __slots__ = ("_module",)

    def __init__(self, module) -> None:
        object.__setattr__(self, "_module", module)

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(
                f"module '{object.__getattribute__(self, '_module').__name__}' has no "
                f"attribute '{name}' -- private attributes are not available to strategy code."
            )
        return getattr(object.__getattribute__(self, "_module"), name)

    def __setattr__(self, name: str, value) -> None:
        raise AttributeError("strategy code cannot modify a module.")

    def __dir__(self) -> list[str]:
        return [a for a in dir(object.__getattribute__(self, "_module")) if not a.startswith("_")]

    def __repr__(self) -> str:
        return f"<module {object.__getattribute__(self, '_module').__name__!r}>"


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    """Replaces __import__ inside the strategy namespace; the argument names
    shadow builtins because they mirror the real __import__ signature. The AST
    scan already rejects denied import statements -- this is the backstop for
    anything that reaches the import machinery some other way."""
    root = name.split(".")[0]
    if level != 0 or root not in _ALLOWED_MODULES:
        raise StrategySecurityError(f"Strategy code may not import '{name}'.")
    # Wrapped, never handed over raw. See _PublicModule: the module a strategy
    # receives must not carry the private aliases that reach the interpreter.
    return _PublicModule(builtins.__import__(name, globals, locals, fromlist, level))


def _build_sandbox_namespace() -> dict:
    safe_builtins = {n: getattr(builtins, n) for n in _SAFE_BUILTIN_NAMES}
    # `class Strategy:` compiles to a __build_class__ call, and the class body
    # reads __name__ to fill in __module__ -- with real builtins that silently
    # resolved to builtins.__name__, so both have to be supplied here.
    safe_builtins["__build_class__"] = builtins.__build_class__
    safe_builtins["__import__"] = _guarded_import
    return {
        "__builtins__": safe_builtins,
        "__name__": "<strategy>",
        # A plain global rather than an entry in _ALLOWED_MODULES: `indicators`
        # is not a real importable module, so allowing the import would have
        # meant teaching _guarded_import to resolve a name that does not exist
        # on sys.path. Injecting it leaves the import allowlist exactly as
        # narrow as it was. See app/services/indicators/__init__.py for what
        # the object does and does not expose.
        "indicators": indicator_namespace(),
    }


def _is_own_attribute(node: ast.Attribute) -> bool:
    """`self._prices` -- the strategy reaching into its own instance.

    Allowed because it is a convention people genuinely use and because `self`
    is the object the strategy already owns; there is nothing there to escape
    to. `self._prices._sys` is still refused, because the outer attribute's
    value is no longer `self`.
    """
    return isinstance(node.value, ast.Name) and node.value.id == "self"


def _format_field_parts(template: str) -> list[str]:
    """The attribute names a format template would walk.

    Parsed leniently and on purpose: this runs to REFUSE things, so a template
    it cannot make sense of should still have its pieces examined rather than
    being waved through.
    """
    parts: list[str] = []
    for chunk in template.split("{")[1:]:
        field = chunk.split("}")[0].split("!")[0].split(":")[0]
        parts.extend(piece for piece in field.replace("[", ".").split(".") if piece)
    return parts


def _reject_unsafe_source(tree: ast.AST) -> None:
    """Static pass over the whole tree, including branches that never run on a
    given tick -- an exfiltration payload hidden in `if price > 9999:` has to
    be caught when the strategy is saved, not months later."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in _ALLOWED_MODULES:
                    raise StrategySecurityError(
                        f"line {node.lineno}: importing '{alias.name}' is not allowed in "
                        f"strategy code. Allowed: {', '.join(sorted(_ALLOWED_MODULES))}."
                    )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level != 0 or module.split(".")[0] not in _ALLOWED_MODULES:
                raise StrategySecurityError(
                    f"line {node.lineno}: importing from '{module or '.'}' is not allowed in "
                    f"strategy code. Allowed: {', '.join(sorted(_ALLOWED_MODULES))}."
                )
        elif isinstance(node, ast.Attribute):
            # ANY leading underscore, not just dunders. This is the hole that
            # was actually exploited: CPython's collections/__init__.py does
            # `import sys as _sys`, so
            #
            #     collections._sys.modules["os"].environ
            #
            # reached the real os module through a module the allow-list
            # deliberately includes, without a dunder anywhere. Every other
            # allowed module is one refactor of the standard library away from
            # offering the same thing, so the rule is about the SHAPE of the
            # access rather than a list of known-bad names.
            #
            # `self._anything` stays legal -- see below. A strategy's own
            # private attributes are a convention people use, and self is the
            # strategy's own object; there is nothing to escape to.
            if node.attr.startswith("_") and not _is_own_attribute(node):
                raise StrategySecurityError(
                    f"line {node.lineno}: accessing '{node.attr}' is not allowed -- private "
                    "attributes are the usual route out of a restricted namespace."
                )
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # A format template is data to the parser and code to str.format.
            # `format` is off the builtin list, but a strategy still holds
            # every string method, so the template itself has to be refused.
            if "{" in node.value and ("." in node.value or "[" in node.value):
                stripped = node.value.replace(" ", "")
                if any(marker in stripped for marker in ("{0.", "{0[", "{}.", "{!")) or any(
                    part.startswith("_") for part in _format_field_parts(node.value)
                ):
                    raise StrategySecurityError(
                        f"line {node.lineno}: this string looks like a format template that "
                        "reaches into an object's attributes, which is how a restricted "
                        "namespace is escaped without naming the attribute in code."
                    )
        elif isinstance(node, ast.Name):
            if node.id in _FORBIDDEN_NAMES or _is_dunder(node.id):
                raise StrategySecurityError(
                    f"line {node.lineno}: '{node.id}' is not available to strategy code."
                )


# --- compilation ------------------------------------------------------------


@dataclass
class LoadedStrategy:
    name: str
    symbol: str
    instance: object
    code_hash: str
    # None means "resolve settings at call time", which also lets a test
    # monkeypatch the setting on an already-cached strategy.
    timeout_sec: float | None = None
    # "on_tick" or "on_bar" -- which of the two shapes this source turned out
    # to be. Every caller that has to treat them differently reads this
    # rather than sniffing the instance again.
    entry_point: str = "on_tick"
    # Only meaningful for an on_bar strategy; kept a real Timeframe for a
    # tick strategy too so nothing downstream has to handle None.
    timeframe: Timeframe = DEFAULT_TIMEFRAME
    # None means the source did not declare one, so the stored per-strategy
    # setting decides. See _effective_warmup() in market_loop.
    warmup_bars: int | None = None
    # The defaults the SOURCE declared, not the values in force. The form shows
    # 「default 5, you set 20」, and reporting the override here would lose the
    # author's answer the moment somebody changed it.
    declared_params: dict = field(default_factory=dict, compare=False)
    # Newest candle this instance has already been shown. Lives here, beside
    # the instance whose state it describes, so it is dropped the moment the
    # source changes and the strategy has to warm up again from scratch.
    last_bar_ts: datetime | None = field(default=None, compare=False)
    _stuck_thread: threading.Thread | None = field(default=None, repr=False, compare=False)

    @property
    def fed_through(self) -> datetime | None:
        """我們餵到哪一根了，就算實例不在了也算數。

        跑在這個行程裡的時候，實例和這筆記帳是同一個物件的兩個欄位——它們一起活、
        一起死，所以這兩個問題的答案永遠一樣。分開命名是因為 `PooledStrategy` 那一
        邊不一樣（實例在子行程裡，記帳在父行程），而 market_loop 兩種都要能問。
        """
        return self.last_bar_ts

    def on_tick(self, price: float) -> str:
        return self._guarded(lambda: self.instance.on_tick(price), "on_tick")

    def on_bar(self, bar: Bar) -> str:
        return self._guarded(lambda: self.instance.on_bar(bar), "on_bar")

    def warm_up(self, bars: list[Bar]) -> None:
        """Replay closed candles to fill the strategy's own memory, throwing
        every signal away.

        Those candles closed before this instance existed, so a BUY they
        produce is an observation about the past, not an instruction for now.
        The whole replay runs inside ONE guarded call rather than one per
        candle: three hundred separate threads would cost more than the
        arithmetic they guard, and the deadline still catches a strategy that
        never returns."""

        def _replay() -> None:
            for bar in bars:
                self.instance.on_bar(bar)

        self._guarded(_replay, "warm_up")

    def _guarded(self, work: Callable[[], object], label: str) -> object:
        """Runs user code under a wall-clock deadline.

        HONEST TRADEOFF: Python cannot kill a thread, so an abandoned call
        keeps running (burning a core on `while True`, or blocked on a socket)
        until the process restarts. What this buys is that the *market loop*
        gets its worker thread back: quotes keep updating and stop-loss /
        take-profit keep firing, instead of the whole poller freezing behind
        one bad strategy. The timeout surfaces as an ordinary strategy error,
        so the existing consecutive-error guard deactivates the strategy after
        _MAX_CONSECUTIVE_ERRORS ticks and the leak stops there.

        A stuck call also keeps ownership of the instance: later ticks are
        refused rather than started alongside it, which would both race on the
        strategy's accumulated self.prices and leak a fresh thread per poll.
        Truly bounding the damage needs a subprocess with its own memory and
        CPU limits -- deliberately out of scope for a single-user app on a
        free-tier box, where an extra process per strategy costs more than the
        failure mode does."""
        timeout = (
            self.timeout_sec if self.timeout_sec is not None else settings.STRATEGY_TICK_TIMEOUT_SEC
        )

        if self._stuck_thread is not None and self._stuck_thread.is_alive():
            raise StrategyTimeoutError(
                f"Previous {label}() call timed out after {timeout}s and is still running; "
                "skipping this tick."
            )

        outcome: dict[str, object] = {}

        def _run() -> None:
            try:
                outcome["value"] = work()
            except Exception as exc:
                outcome["error"] = exc

        worker = threading.Thread(target=_run, name=f"strategy-{self.name}", daemon=True)
        worker.start()
        worker.join(timeout)

        if worker.is_alive():
            self._stuck_thread = worker
            raise StrategyTimeoutError(f"{label}() timed out after {timeout}s.")

        self._stuck_thread = None
        if "error" in outcome:
            raise outcome["error"]
        if "value" not in outcome:
            # Only reachable if the strategy raised a BaseException (SystemExit,
            # KeyboardInterrupt) -- the thread died without recording anything.
            raise StrategyValidationError(f"{label}() exited without completing.")
        return outcome["value"]


def code_hash(source_code: str) -> str:
    return hashlib.sha256(source_code.encode("utf-8")).hexdigest()


def _detect_entry_point(instance: object) -> str:
    """Which of the two shapes this strategy is written in.

    Defining both is rejected rather than resolved by precedence: whichever
    one the runtime picked, the other half of the code would look live and be
    dead -- source that reads correct while doing nothing is the exact
    failure this entry point was added to remove."""
    has_tick = callable(getattr(instance, "on_tick", None))
    has_bar = callable(getattr(instance, "on_bar", None))

    if has_tick and has_bar:
        raise StrategyValidationError(
            "Strategy defines both on_tick and on_bar -- pick one. on_tick(self, "
            "current_price) runs on every price update; on_bar(self, bar) runs once per "
            "closed candle of self.timeframe."
        )
    if has_tick:
        return "on_tick"
    if has_bar:
        return "on_bar"
    raise StrategyValidationError(
        "Strategy must define on_tick(self, current_price) -- called on every price "
        "update -- or on_bar(self, bar), called once per closed candle."
    )


def _read_timeframe(instance: object) -> Timeframe:
    declared = getattr(instance, "timeframe", None)
    if declared is None:
        return DEFAULT_TIMEFRAME
    try:
        return Timeframe(declared)
    except ValueError as exc:
        allowed = ", ".join(tf.value for tf in Timeframe)
        raise StrategyValidationError(
            f"self.timeframe = {declared!r} is not a candle size this runtime can fetch. "
            f"Use one of: {allowed}."
        ) from exc


def _read_warmup_bars(instance: object) -> int | None:
    declared = getattr(instance, "warmup_bars", None)
    if declared is None:
        return None
    if not isinstance(declared, int) or isinstance(declared, bool) or declared < 0:
        raise StrategyValidationError(
            f"self.warmup_bars = {declared!r} must be a non-negative whole number of candles."
        )
    return declared


# What a parameter may be. Anything a form can render and JSON can carry --
# and nothing else, because a parameter that shows up in the API and has no
# input box is a parameter nobody can ever change, which is the situation this
# whole feature exists to end.
_PARAM_TYPES = (bool, int, float, str)


def _read_params(instance: object) -> dict:
    """The defaults the author declared, checked at compile time.

    Compile time rather than first tick: the author is looking at the editor
    now, and a shape error that surfaces during a live poll surfaces as a
    strategy that switched itself off.
    """
    declared = getattr(instance, "params", None)
    if declared is None:
        return {}
    if not isinstance(declared, dict):
        raise StrategyValidationError(
            f"self.params = {declared!r} must be a dict of名稱 to default value, "
            'for example {"window": 5}.'
        )
    for key, value in declared.items():
        if not isinstance(key, str):
            raise StrategyValidationError(f"self.params 的鍵 {key!r} 必須是字串。")
        if not isinstance(value, _PARAM_TYPES):
            raise StrategyValidationError(
                f"self.params[{key!r}] 是 {type(value).__name__}，沒有辦法在畫面上編輯。"
                "只能用數字、真假值或文字。"
            )
    return dict(declared)


def _apply_params(instance: object, declared: dict, overrides: dict) -> None:
    """Merge the owner's settings into the instance's own dict.

    AFTER __init__, because that is the only injection point that does not
    change the constructor signature every existing strategy already uses. The
    consequence is a real one and is documented everywhere this is described: a
    value copied out of self.params into another attribute during __init__ will
    not see the override. Read the dict where the decision is made.
    """
    for key, value in overrides.items():
        if key not in declared:
            # Refused, not dropped. It means the source was edited and this
            # stored setting is now about something that no longer exists;
            # keeping it quietly leaves a value in the database that the owner
            # believes is doing something.
            raise StrategyValidationError(
                f"這個策略沒有叫做 {key!r} 的參數。"
                f"它宣告的是：{', '.join(sorted(declared)) or '（沒有參數）'}。"
            )
        default = declared[key]
        if isinstance(default, bool):
            ok = isinstance(value, bool)
        elif isinstance(default, int):
            # bool is a subclass of int, so True would otherwise pass as a
            # number -- and a checkbox value in a numeric field compares in
            # ways nobody predicts.
            ok = isinstance(value, int) and not isinstance(value, bool)
        elif isinstance(default, float):
            # A whole number typed into a box whose default is 1.5 is not a
            # mistake, and JSON has no way to write 「2.0」.
            ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        else:
            ok = isinstance(value, str)
        if not ok:
            raise StrategyValidationError(
                f"參數 {key!r} 應該是 {type(default).__name__}，但收到 "
                f"{type(value).__name__}（{value!r}）。"
            )
        instance.params[key] = value


def compile_strategy(
    source_code: str, timeout_sec: float | None = None, params: dict | None = None
) -> LoadedStrategy:
    """Compiles user-authored strategy source into a live `LoadedStrategy`.

    Expects `class Strategy: def __init__(self): self.name=...;
    self.symbol=...` plus exactly one entry point: the legacy
    `def on_tick(self, current_price: float) -> str`, unchanged and still the
    default, or `def on_bar(self, bar) -> str` for a strategy that reasons in
    candles of `self.timeframe`."""
    try:
        tree = ast.parse(source_code, "<strategy>", "exec")
    except SyntaxError as exc:
        raise StrategyValidationError(f"Strategy code failed to compile/run: {exc}") from exc

    _reject_unsafe_source(tree)

    namespace = _build_sandbox_namespace()
    try:
        # Running owner-authored strategy code IS the feature, and this is
        # the sandbox that exists to make it survivable. The tree
        # has already been through _reject_unsafe_source() immediately above:
        # an AST walk that refuses imports outside the allow-list, every name
        # in _FORBIDDEN_NAMES, and every dunder. The namespace is built by
        # _build_sandbox_namespace() rather than inherited. Removing the exec
        # would remove the product; what is reviewable is the validator, and
        # tests/test_indicators_sandbox.py is where that is held to account.
        exec(compile(tree, "<strategy>", "exec"), namespace)  # nosec B102
    except StrategySecurityError:
        raise
    except Exception as exc:
        raise StrategyValidationError(f"Strategy code failed to compile/run: {exc}") from exc

    strategy_cls = namespace.get("Strategy")
    if strategy_cls is None:
        raise StrategyValidationError("Strategy code must define a `class Strategy`.")

    try:
        instance = strategy_cls()
    except Exception as exc:
        raise StrategyValidationError(f"Strategy() failed to instantiate: {exc}") from exc

    for attr in ("name", "symbol"):
        if not hasattr(instance, attr):
            raise StrategyValidationError(f"Strategy instance is missing required '{attr}'.")

    declared = _read_params(instance)
    if params:
        _apply_params(instance, declared, params)

    return LoadedStrategy(
        name=instance.name,
        symbol=instance.symbol,
        instance=instance,
        code_hash=code_hash(source_code),
        timeout_sec=timeout_sec,
        entry_point=_detect_entry_point(instance),
        timeframe=_read_timeframe(instance),
        warmup_bars=_read_warmup_bars(instance),
        declared_params=declared,
    )


def effective_warmup(loaded: LoadedStrategy, stored_default: int) -> int:
    """How many closed candles must exist before this strategy may signal.

    The number is a property of the indicator, which lives in the source, so a
    `self.warmup_bars` declaration wins outright; the stored default (the
    Strategy.warmup_bars column, or a backtest request's own value) is the
    fallback for source that says nothing. Deliberately not max() of the two:
    the stored default of 30 is a generic number nobody chose, and holding a
    strategy that needs 5 weekly candles back for 30 weeks because of it would
    be a bug wearing a safety jacket.

    Lives beside the runtime rather than in the market loop because a backtest
    has to warm up by exactly the same rule -- if the two ever disagreed, the
    backtest would be scoring a strategy that starts signalling on a different
    candle than the live one does.
    """
    return loaded.warmup_bars if loaded.warmup_bars is not None else stored_default


class StrategyRegistry:
    """Caches live Strategy instances by strategy id, keyed additionally by a
    content hash of the source. The legacy `Strategy` class accumulates state
    across ticks in `self.prices`, so re-using the same instance (rather than
    recompiling every poll) is what lets an MA5/MA20 strategy actually work --
    a fresh instance every tick would never see more than one price."""

    def __init__(self) -> None:
        self._cache: dict[int, LoadedStrategy] = {}
        # The parameters each cached instance was built with. Kept alongside
        # rather than on LoadedStrategy: what is cached is the SOURCE's answer,
        # and this is the question that was asked of it.
        self._params: dict[int, dict] = {}

    def get_or_load(
        self, strategy_id: int, source_code: str, params: dict | None = None
    ) -> LoadedStrategy:
        current_hash = code_hash(source_code)
        # The parameters are part of what 「this strategy」 means. Keyed on the
        # source alone, saving a new value would leave the old instance
        # running: the form would show 20 and the strategy would keep using 5,
        # forever, with nothing anywhere saying so.
        current_params = dict(params or {})
        cached = self._cache.get(strategy_id)
        if cached is not None and cached.code_hash == current_hash:
            if self._params.get(strategy_id) == current_params:
                return cached

        loaded = compile_strategy(source_code, params=params)
        self._cache[strategy_id] = loaded
        self._params[strategy_id] = current_params
        return loaded

    def invalidate(self, strategy_id: int) -> None:
        """Drop the live instance, so the next load starts from scratch.

        Call this whenever a strategy stops being the thing that is running:
        paused, deleted, or edited in a way the content hash cannot see (the
        symbol, for one -- the accumulated `self.prices` belong to the old
        symbol and would seed the new one's moving average)."""
        self._cache.pop(strategy_id, None)
        self._params.pop(strategy_id, None)

    def is_cached(self, strategy_id: int) -> bool:
        return strategy_id in self._cache
