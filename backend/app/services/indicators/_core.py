"""Shared arithmetic and input checking for the indicator library.

WHY THIS IS HAND-ROLLED RATHER THAN A DEPENDENCY. The obvious move was to
pull in `ta` (the maintained pure-Python TA package; TA-Lib itself needs a C
build the python:3.13-slim image does not have). It was evaluated and
rejected on the numbers: on Wilder's own published RSI example `ta` is wrong
by up to 3.7 RSI points, because it smooths with a plain EWM seeded from the
first sample instead of Wilder's SMA-seeded running average. Its ATR is a bar
early for the same family of reason, and its PSAR leaves un-computed bars
filled with the close. Those are exactly the "runs, returns plausible
numbers, is wrong" failures this library exists to prevent, and an RSI three
points out is the difference between the owner's "RSI > 80" trigger firing
and not firing. Adding a dependency that has to be fought on its own core
indicators buys nothing; the tests, not the package, are what buy correctness.
`ta` is still used as an independent cross-check in development, and the
tests record where it agrees and where it does not.

TWO CONVENTIONS, APPLIED EVERYWHERE, so that nothing here needs remembering
case by case:

1. ALIGNMENT. Every series-returning indicator returns a list exactly as long
   as its input, `None` in every position where there is not yet enough
   history. `series[-1]` is therefore always "now" and `series[-2]` always
   "the previous candle", whatever the period -- which is what makes a
   crossover test safe to write. Warming up is never an error: a strategy
   saved this morning has three candles and needs thirty, and raising there
   would trip the market loop's consecutive-error guard and retire it.

2. SEEDING. Every recursive average -- EMA, Wilder's smoothing, KAMA -- is
   seeded with the simple mean of its first `period` inputs, then runs
   recursively from there. That is what TA-Lib, StockCharts and Pine Script's
   ta.ema() all do, and it is the single most common thing to get wrong.

Plain lists of floats in, plain lists of floats out: no numpy or pandas
object ever crosses into strategy code, so the sandbox keeps handing out only
built-in types.

COST. Each call is linear in the series it is handed, and a strategy calls
them once per candle, so replaying a warm-up is quadratic in the history the
strategy chooses to keep. Measured worst case on the free-tier box: the
market loop's 300-candle warm-up, with five indicators recomputed from an
uncapped history on every candle, takes ~1.3s against
STRATEGY_TICK_TIMEOUT_SEC = 2.0. It fits, but a strategy that keeps only the
window it needs (the idiom in both shipped samples) costs about half that,
which is why the AI contract tells the model to trim.
"""

import math
from collections.abc import Sequence

# A value per input position; None wherever the indicator has not warmed up.
Series = list[float | None]


def numbers(values: Sequence[float], name: str) -> list[float]:
    """Validate one input series and return it as plain floats.

    Fails loudly on a None or a NaN rather than skipping it. Bar.volume is
    `float | None` upstream, so a strategy that appends it blindly will hand
    us a hole, and an average quietly computed over a shorter window is worse
    than an error the consecutive-error guard can act on.
    """
    if isinstance(values, str | bytes):
        raise ValueError(f"{name} must be a list of numbers, not a string.")
    try:
        out = [float(v) for v in values]
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{name} must be a list of numbers -- one entry is not a number ({exc})."
        ) from exc
    for value in out:
        if not math.isfinite(value):
            raise ValueError(f"{name} contains {value}, which no indicator can average.")
    return out


def aligned(**series: Sequence[float]) -> list[list[float]]:
    """Validate several series that must describe the same bars.

    Length is checked by name because the mistake this catches -- passing
    highs and lows that have drifted a bar apart -- silently computes a range
    between two different candles.
    """
    checked = {name: numbers(values, name) for name, values in series.items()}
    lengths = {name: len(values) for name, values in checked.items()}
    if len(set(lengths.values())) > 1:
        detail = ", ".join(f"{name}={length}" for name, length in lengths.items())
        raise ValueError(f"every series must cover the same bars, but got {detail}.")
    return list(checked.values())


def period(value: int, name: str = "period", minimum: int = 1) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{name} must be a whole number of at least {minimum}, not {value!r}.")
    return value


def multiplier(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a number, not {value!r}.")
    if not math.isfinite(float(value)):
        raise ValueError(f"{name} must be a finite number, not {value!r}.")
    return float(value)


def blank(length: int) -> Series:
    return [None] * length


def first_value_index(series: Series) -> int:
    """Where the settled part of a series starts; len(series) if it never does.

    Every series here is leading-Nones-then-numbers, so one index describes
    the whole warm-up.
    """
    for index, value in enumerate(series):
        if value is not None:
            return index
    return len(series)


def _replace_tail(length: int, start: int, values: list[float]) -> Series:
    out: Series = [None] * length
    out[start : start + len(values)] = values
    return out


def sma_values(values: list[float], size: int) -> Series:
    """Rolling mean, computed by running total rather than re-summing each
    window -- warm-up replays a few hundred candles through every indicator a
    strategy uses, on a free-tier box."""
    if len(values) < size:
        return blank(len(values))
    out = blank(len(values))
    total = sum(values[:size])
    out[size - 1] = total / size
    for i in range(size, len(values)):
        total += values[i] - values[i - size]
        out[i] = total / size
    return out


def ema_values(values: list[float], size: int) -> Series:
    if len(values) < size:
        return blank(len(values))
    alpha = 2.0 / (size + 1)
    out = blank(len(values))
    current = sum(values[:size]) / size  # the SMA seed -- see the module docstring
    out[size - 1] = current
    for i in range(size, len(values)):
        current += (values[i] - current) * alpha
        out[i] = current
    return out


def wma_values(values: list[float], size: int) -> Series:
    if len(values) < size:
        return blank(len(values))
    denominator = size * (size + 1) / 2
    out = blank(len(values))
    for i in range(size - 1, len(values)):
        window = values[i - size + 1 : i + 1]
        out[i] = sum(value * weight for weight, value in enumerate(window, start=1)) / denominator
    return out


def wilder_values(values: list[float], size: int) -> Series:
    """Wilder's smoothing: mean of the first `size` samples, then
    prev + (new - prev)/size.

    Distinct from an EMA of the same length -- Wilder's effective alpha is
    1/n where an EMA's is 2/(n+1) -- and mixing them up is the classic way to
    produce an RSI or ATR that is close enough to look right.
    """
    if len(values) < size:
        return blank(len(values))
    out = blank(len(values))
    current = sum(values[:size]) / size
    out[size - 1] = current
    for i in range(size, len(values)):
        current += (values[i] - current) / size
        out[i] = current
    return out


def _over_settled(series: Series, size: int, kernel) -> Series:
    """Run one of the kernels above over a series that already has a warm-up,
    keeping the result aligned with the original."""
    start = first_value_index(series)
    settled = [value for value in series[start:] if value is not None]
    if len(settled) < size:
        return blank(len(series))
    return _replace_tail(len(series), start, kernel(settled, size))


def sma_of(series: Series, size: int) -> Series:
    return _over_settled(series, size, sma_values)


def ema_of(series: Series, size: int) -> Series:
    return _over_settled(series, size, ema_values)


def wma_of(series: Series, size: int) -> Series:
    return _over_settled(series, size, wma_values)


def wilder_of(series: Series, size: int) -> Series:
    return _over_settled(series, size, wilder_values)


def true_ranges(highs: list[float], lows: list[float], closes: list[float]) -> Series:
    """max(high-low, |high-prev_close|, |low-prev_close|), None at bar 0.

    Bar 0 has no previous close, so it has no true range. Giving it one --
    usually its plain high-low -- shifts every Wilder average built on it a
    bar earlier, which is what the `ta` package does to its ATR.
    """
    out = blank(len(closes))
    for i in range(1, len(closes)):
        previous = closes[i - 1]
        out[i] = max(highs[i] - lows[i], abs(highs[i] - previous), abs(lows[i] - previous))
    return out


def typical_prices(highs: list[float], lows: list[float], closes: list[float]) -> list[float]:
    return [(high + low + close) / 3 for high, low, close in zip(highs, lows, closes, strict=True)]


def rolling_max(values: list[float], size: int) -> Series:
    out = blank(len(values))
    for i in range(size - 1, len(values)):
        out[i] = max(values[i - size + 1 : i + 1])
    return out


def rolling_min(values: list[float], size: int) -> Series:
    out = blank(len(values))
    for i in range(size - 1, len(values)):
        out[i] = min(values[i - size + 1 : i + 1])
    return out


def rolling_sum(values: list[float], size: int) -> Series:
    if len(values) < size:
        return blank(len(values))
    out = blank(len(values))
    total = sum(values[:size])
    out[size - 1] = total
    for i in range(size, len(values)):
        total += values[i] - values[i - size]
        out[i] = total
    return out


def prepend_gap(series: Series, length: int) -> Series:
    """Re-align a series that was computed from bar-to-bar pairs.

    Those come out one entry short -- bar 0 has no predecessor to compare
    against -- so they get a leading None. The length guard matters: no bars
    in must mean no bars out, not a single None, or the caller's
    `series[-1] is None` warm-up check reads as "warming up" on an input that
    never had a bar 0 at all.
    """
    return [None, *series] if length else []


def combine(*series: Series, using) -> Series:
    """Apply `using` position by position, yielding None wherever any input is
    still warming up. Keeps every derived series aligned without each caller
    writing the same None-guard."""
    length = len(series[0])
    out = blank(length)
    for i in range(length):
        parts = [s[i] for s in series]
        if any(part is None for part in parts):
            continue
        out[i] = using(*parts)
    return out
