import hashlib
from dataclasses import dataclass


class StrategyValidationError(Exception):
    pass


@dataclass
class LoadedStrategy:
    name: str
    symbol: str
    instance: object
    code_hash: str

    def on_tick(self, price: float) -> str:
        return self.instance.on_tick(price)


def code_hash(source_code: str) -> str:
    return hashlib.sha256(source_code.encode("utf-8")).hexdigest()


def compile_strategy(source_code: str) -> LoadedStrategy:
    """Compiles user-authored strategy source into a live `LoadedStrategy`.

    Expects the legacy `class Strategy: def __init__(self): self.name=...;
    self.symbol=...` / `def on_tick(self, current_price: float) -> str`
    shape -- this is the interface the user already writes strategies in and
    it's being preserved as-is."""
    namespace: dict = {}
    try:
        compiled = compile(source_code, "<strategy>", "exec")
        exec(compiled, namespace)
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
