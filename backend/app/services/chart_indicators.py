"""Indicator values for the chart, computed by the code the strategies use.

THE CONSTRAINT THIS EXISTS TO SATISFY. If the chart draws one moving average
and a strategy trades a different one, the owner is looking at a picture of
something that is not happening. That is the worst outcome this feature can
produce, and it is worse than having no indicators at all.

It is satisfied BY CONSTRUCTION rather than by discipline: `spec.fn` is the very
object `indicators/__init__.py` puts into the strategy sandbox's namespace.
Calling it here calls the same function the strategy calls. There is no second
implementation to keep in step, and there must never be one in TypeScript.

WHAT ARRIVES ON THE WIRE, and why each decision:

  {time, value} pairs, never a positional array. A bare array has to be zipped
  by index against the chart's candles, and one bar of drift draws a moving
  average shifted sideways with nothing on screen saying so.

  Warm-up positions are OMITTED, not sent as null. Every indicator returns a
  list exactly as long as its input with leading Nones -- measured across all
  40 -- and null is not a point a line renderer can draw.

CUMULATIVE INDICATORS ARE RELATIVE TO THE WINDOW. Measured: ema, kama, rsi, atr,
parabolic_sar and sma converge to identical values whether computed over 250 or
1000 bars (drift 0.0000%), so a longer window buys nothing. obv does NOT -- it
drifts 71.5%, because it is a running sum with no decay, and no window short of
the instrument's entire history would fix that. Same for
accumulation_distribution and vwap. Their LEVEL is arbitrary; their SHAPE is
not, which is all anybody reads them for. Stated rather than papered over.
"""

import math
from dataclasses import dataclass
from datetime import datetime

from app.services.indicator_panes import UNCHARTABLE, pane_for, scale_for
from app.services.indicators import catalogue
from app.services.market_data.base import Bar

# How many indicator outputs one request may ask for. Measured on this machine:
# all 39 chartable indicators over 250 bars is 87 ms, over 1000 bars 542 ms. The
# cap is not about that arithmetic -- it is that this runs in the same process
# as the market loop, and a page that refetches on focus must not be able to
# spend half a second of it on a whim.
MAX_INDICATORS_PER_REQUEST = 8


@dataclass(frozen=True)
class IndicatorPoint:
    time: datetime
    value: float


@dataclass(frozen=True)
class IndicatorSeries:
    name: str
    key: str
    pane: str
    # Which other series this one may be measured against. Two series in one
    # pane sharing this string share an axis; see indicator_panes.scale_for.
    scale: str
    points: list[IndicatorPoint]


class IndicatorRequestError(ValueError):
    """The request cannot be computed, and saying why beats drawing nothing."""


def _specs() -> dict:
    return {spec.name: spec for spec in catalogue()}


def _columns(bars: list[Bar]) -> dict[str, list[float]]:
    """The bar series, under the names the indicator functions ask for.

    Bound BY PARAMETER NAME rather than by position: the 40 functions take
    different subsets in different orders, and positional binding would hand
    `lows` to a parameter called `highs` on the first signature that differs.
    """
    return {
        "values": [float(b.close) for b in bars],
        "closes": [float(b.close) for b in bars],
        "opens": [float(b.open) for b in bars],
        "highs": [float(b.high) for b in bars],
        "lows": [float(b.low) for b in bars],
        # Bar.volume is optional -- the provider's NaN guard covers OHLC only,
        # so a row padded over a halt arrives with none. Zero is the honest
        # stand-in for a bar that traded nothing, and it is what keeps the
        # eight volume indicators from raising on one gap.
        "volumes": [float(b.volume) if b.volume is not None else 0.0 for b in bars],
    }


def compute(bars: list[Bar], requests: list[dict]) -> list[IndicatorSeries]:
    """One series per requested output, aligned to the bars' own timestamps."""
    if len(requests) > MAX_INDICATORS_PER_REQUEST:
        raise IndicatorRequestError(
            f"一次最多 {MAX_INDICATORS_PER_REQUEST} 個指標，收到 {len(requests)} 個。"
        )
    if not bars:
        return []

    specs = _specs()
    columns = _columns(bars)
    times = [b.timestamp for b in bars]
    out: list[IndicatorSeries] = []

    for request in requests:
        name = str(request.get("name") or "").strip()
        spec = specs.get(name)
        if spec is None:
            raise IndicatorRequestError(f"沒有這個指標：{name}")
        if name in UNCHARTABLE:
            # pivot_points returns seven scalars, not series. Refused here so a
            # request that cannot be drawn fails with a sentence instead of an
            # empty chart.
            raise IndicatorRequestError(f"{name} 不是可以畫在圖上的指標。")

        supplied = request.get("params") or {}
        kwargs: dict = {}
        for param in spec.params:
            if param.type.startswith("list"):
                if param.name not in columns:
                    raise IndicatorRequestError(f"{name} 需要這個 app 沒有的資料：{param.name}")
                kwargs[param.name] = columns[param.name]
            elif param.name in supplied:
                kwargs[param.name] = _coerce(name, param, supplied[param.name])

        try:
            result = spec.fn(**kwargs)
        except Exception as exc:
            # One bad parameter must not 500 the chart. A period longer than
            # the window, a negative length: the library raises, and the page
            # can only act on a sentence.
            raise IndicatorRequestError(f"{name} 算不出來：{exc}") from exc

        series = result if isinstance(result, dict) else {"": result}
        before = len(out)
        for key, values in series.items():
            if not isinstance(values, list):
                raise IndicatorRequestError(f"{name}.{key} 不是可以畫成線的資料。")
            if len(values) != len(times):
                # Measured across all 40: every indicator returns a list exactly
                # as long as its input, with leading Nones for the warm-up. If
                # one ever stops doing that, zip() would align the two from the
                # START and quietly shift the whole line sideways -- a
                # plausible, well-formed, WRONG chart, which is the failure this
                # app treats as worse than a blank one.
                raise IndicatorRequestError(
                    f"{name}.{key} 回傳 {len(values)} 個值，但有 {len(times)} 根 K 棒，對不起來。"
                )
            out.append(
                IndicatorSeries(
                    name=name,
                    key=key,
                    pane=pane_for(name, key),
                    scale=scale_for(name, key),
                    points=[
                        IndicatorPoint(time=time, value=float(value))
                        # Omitted, not nulled: every indicator returns a list as
                        # long as its input with leading Nones for the warm-up,
                        # and a null is not a point a line renderer can draw.
                        #
                        # NaN and inf are dropped the same way, and for a
                        # sharper reason: Python's json writes them as the bare
                        # tokens NaN and Infinity, which are not JSON, so
                        # JSON.parse in the browser throws and the whole
                        # response is lost over one bad point. Measured: no
                        # indicator produces one from flat prices, zero prices
                        # or extreme swings -- only from volumes around 1e308,
                        # which no provider returns. It is one condition
                        # against an illegible failure.
                        for time, value in zip(times, values, strict=False)
                        if _finite(value)
                    ],
                )
            )

        # MEASURED: sma(period=9999) over 250 bars does not raise. It returns
        # 250 Nones -- every position is warm-up -- so every point is dropped
        # and the answer is a series with nothing in it. On the chart that is
        # an empty line and not one word about why, which is the failure this
        # app treats as worse than an error. Somebody who typed a number bigger
        # than their window has to be told so.
        if all(not item.points for item in out[before:]):
            raise IndicatorRequestError(
                f"{name} 在這 {len(times)} 根 K 棒上算不出任何值 —— 週期可能比資料還長。"
            )

    return out


def _finite(value) -> bool:
    """A number a line renderer can plot: not None, not a bool, not NaN/inf."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _coerce(name: str, param, value):
    """A JSON number into the type the function declared.

    JSON has one number type, so a period of 20 arrives as an int whether the
    signature wanted int or float, and a float where an int was declared makes
    range() raise deep inside the library.
    """
    if isinstance(value, bool):
        raise IndicatorRequestError(f"{name} 的 {param.name} 不是真假值。")
    # CHECKED BEFORE THE try, because IndicatorRequestError subclasses
    # ValueError: raised inside, this very sentence would be caught by the
    # `except (TypeError, ValueError)` below and replaced with 「不是數字」 --
    # which is false, 20.5 is a number, and it sends the reader looking for a
    # mistake they did not make.
    if param.type == "int" and isinstance(value, float) and not value.is_integer():
        raise IndicatorRequestError(f"{name} 的 {param.name} 必須是整數。")
    try:
        if param.type == "int":
            return int(value)
        if param.type == "float":
            return float(value)
    except (TypeError, ValueError) as exc:
        raise IndicatorRequestError(f"{name} 的 {param.name} 不是數字：{value!r}") from exc
    return value
