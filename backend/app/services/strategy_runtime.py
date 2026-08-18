import ast
import builtins
import hashlib
import threading
from dataclasses import dataclass, field

from app.config import settings


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
        "enumerate", "filter", "float", "format", "frozenset", "hasattr", "hash", "int",
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


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    """Replaces __import__ inside the strategy namespace; the argument names
    shadow builtins because they mirror the real __import__ signature. The AST
    scan already rejects denied import statements -- this is the backstop for
    anything that reaches the import machinery some other way."""
    root = name.split(".")[0]
    if level != 0 or root not in _ALLOWED_MODULES:
        raise StrategySecurityError(f"Strategy code may not import '{name}'.")
    return builtins.__import__(name, globals, locals, fromlist, level)


def _build_sandbox_namespace() -> dict:
    safe_builtins = {n: getattr(builtins, n) for n in _SAFE_BUILTIN_NAMES}
    # `class Strategy:` compiles to a __build_class__ call, and the class body
    # reads __name__ to fill in __module__ -- with real builtins that silently
    # resolved to builtins.__name__, so both have to be supplied here.
    safe_builtins["__build_class__"] = builtins.__build_class__
    safe_builtins["__import__"] = _guarded_import
    return {"__builtins__": safe_builtins, "__name__": "<strategy>"}


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
            if _is_dunder(node.attr):
                raise StrategySecurityError(
                    f"line {node.lineno}: accessing '{node.attr}' is not allowed -- dunder "
                    "attributes are the usual route out of a restricted namespace."
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
    _stuck_thread: threading.Thread | None = field(default=None, repr=False, compare=False)

    def on_tick(self, price: float) -> str:
        """Runs the user's on_tick under a wall-clock deadline.

        HONEST TRADEOFF: Python cannot kill a thread, so an abandoned on_tick
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
            self.timeout_sec
            if self.timeout_sec is not None
            else settings.STRATEGY_TICK_TIMEOUT_SEC
        )

        if self._stuck_thread is not None and self._stuck_thread.is_alive():
            raise StrategyTimeoutError(
                f"Previous on_tick() call timed out after {timeout}s and is still running; "
                "skipping this tick."
            )

        outcome: dict[str, object] = {}

        def _run() -> None:
            try:
                outcome["value"] = self.instance.on_tick(price)
            except Exception as exc:
                outcome["error"] = exc

        worker = threading.Thread(target=_run, name=f"strategy-{self.name}", daemon=True)
        worker.start()
        worker.join(timeout)

        if worker.is_alive():
            self._stuck_thread = worker
            raise StrategyTimeoutError(f"on_tick() timed out after {timeout}s.")

        self._stuck_thread = None
        if "error" in outcome:
            raise outcome["error"]
        if "value" not in outcome:
            # Only reachable if on_tick raised a BaseException (SystemExit,
            # KeyboardInterrupt) -- the thread died without recording anything.
            raise StrategyValidationError("on_tick() exited without returning a signal.")
        return outcome["value"]


def code_hash(source_code: str) -> str:
    return hashlib.sha256(source_code.encode("utf-8")).hexdigest()


def compile_strategy(source_code: str, timeout_sec: float | None = None) -> LoadedStrategy:
    """Compiles user-authored strategy source into a live `LoadedStrategy`.

    Expects the legacy `class Strategy: def __init__(self): self.name=...;
    self.symbol=...` / `def on_tick(self, current_price: float) -> str`
    shape -- this is the interface the user already writes strategies in and
    it's being preserved as-is."""
    try:
        tree = ast.parse(source_code, "<strategy>", "exec")
    except SyntaxError as exc:
        raise StrategyValidationError(f"Strategy code failed to compile/run: {exc}") from exc

    _reject_unsafe_source(tree)

    namespace = _build_sandbox_namespace()
    try:
        exec(compile(tree, "<strategy>", "exec"), namespace)
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

    for attr in ("name", "symbol", "on_tick"):
        if not hasattr(instance, attr):
            raise StrategyValidationError(f"Strategy instance is missing required '{attr}'.")
    if not callable(instance.on_tick):
        raise StrategyValidationError("Strategy.on_tick must be callable.")

    return LoadedStrategy(
        name=instance.name,
        symbol=instance.symbol,
        instance=instance,
        code_hash=code_hash(source_code),
        timeout_sec=timeout_sec,
    )


class StrategyRegistry:
    """Caches live Strategy instances by strategy id, keyed additionally by a
    content hash of the source. The legacy `Strategy` class accumulates state
    across ticks in `self.prices`, so re-using the same instance (rather than
    recompiling every poll) is what lets an MA5/MA20 strategy actually work --
    a fresh instance every tick would never see more than one price."""

    def __init__(self) -> None:
        self._cache: dict[int, LoadedStrategy] = {}

    def get_or_load(self, strategy_id: int, source_code: str) -> LoadedStrategy:
        current_hash = code_hash(source_code)
        cached = self._cache.get(strategy_id)
        if cached is not None and cached.code_hash == current_hash:
            return cached

        loaded = compile_strategy(source_code)
        self._cache[strategy_id] = loaded
        return loaded

    def invalidate(self, strategy_id: int) -> None:
        self._cache.pop(strategy_id, None)
