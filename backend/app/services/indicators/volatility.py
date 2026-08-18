"""Volatility indicators: how far price is spreading, and channels built on it."""

import math

from app.services.indicators import _core
from app.services.indicators._core import Series
from app.services.indicators.registry import IndicatorCategory, IndicatorResult, indicator

_VOLATILITY = IndicatorCategory.VOLATILITY


@indicator(
    category=_VOLATILITY,
    title="標準差 (Standard Deviation)",
    description=(
        "最近 period 根的母體標準差（除以 n，不是 n-1）。"
        "布林通道就是用這個定義，改用樣本標準差會讓 20 期通道憑空寬約 2.6%。"
    ),
)
def stdev(values: list[float], period: int = 20) -> Series:
    checked = _core.numbers(values, "values")
    size = _core.period(period)
    out = _core.blank(len(checked))
    for i in range(size - 1, len(checked)):
        window = checked[i - size + 1 : i + 1]
        mean = sum(window) / size
        out[i] = math.sqrt(sum((value - mean) ** 2 for value in window) / size)
    return out


@indicator(
    category=_VOLATILITY,
    title="布林通道 (Bollinger Bands)",
    description=(
        "以 period 期 SMA 為中軌，上下各 num_std 個標準差。"
        "bandwidth 是通道寬度占中軌的百分比（用來看「壓縮」），"
        "percent_b 是價格在通道中的位置（1 = 上軌、0 = 下軌）；通道寬度為零時 percent_b 為 None。"
    ),
    result=IndicatorResult.SERIES_MAP,
    keys=("upper", "middle", "lower", "bandwidth", "percent_b"),
)
def bollinger_bands(
    values: list[float], period: int = 20, num_std: float = 2.0
) -> dict[str, Series]:
    checked = _core.numbers(values, "values")
    size = _core.period(period)
    width = _core.multiplier(num_std, "num_std")

    middle = _core.sma_values(checked, size)
    deviation = stdev(checked, size)
    upper = _core.combine(middle, deviation, using=lambda mid, sd: mid + width * sd)
    lower = _core.combine(middle, deviation, using=lambda mid, sd: mid - width * sd)

    percent_b = _core.blank(len(checked))
    bandwidth = _core.blank(len(checked))
    for i in range(len(checked)):
        if upper[i] is None:
            continue
        span = upper[i] - lower[i]
        bandwidth[i] = 0.0 if middle[i] == 0 else 100.0 * span / middle[i]
        if span != 0:
            percent_b[i] = (checked[i] - lower[i]) / span
    return {
        "upper": upper,
        "middle": middle,
        "lower": lower,
        "bandwidth": bandwidth,
        "percent_b": percent_b,
    }


@indicator(
    category=_VOLATILITY,
    title="平均真實區間 (ATR)",
    description=(
        "真實區間 = max(高-低, |高-前收|, |低-前收|)，把跳空也算進波動，再用 Wilder 平滑。"
        "第 0 根沒有前收盤價所以沒有真實區間，第一個 ATR 落在第 period 根。ATR 沒有方向，只有幅度。"
    ),
)
def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> Series:
    high, low, close = _core.aligned(highs=highs, lows=lows, closes=closes)
    return _core.wilder_of(_core.true_ranges(high, low, close), _core.period(period))


@indicator(
    category=_VOLATILITY,
    title="肯特納通道 (Keltner Channels)",
    description=(
        "中軌是 period 期 EMA，上下軌是中軌加減 multiplier 倍的 ATR(atr_period)。"
        "與布林通道的差別在於寬度來自 ATR 而非標準差，因此對跳空更敏感、對盤整更穩定。"
    ),
    result=IndicatorResult.SERIES_MAP,
    keys=("upper", "middle", "lower"),
)
def keltner_channels(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 20,
    atr_period: int = 10,
    multiplier: float = 2.0,
) -> dict[str, Series]:
    high, low, close = _core.aligned(highs=highs, lows=lows, closes=closes)
    width = _core.multiplier(multiplier, "multiplier")
    middle = _core.ema_values(close, _core.period(period))
    ranges = _core.wilder_of(
        _core.true_ranges(high, low, close), _core.period(atr_period, "atr_period")
    )
    return {
        "upper": _core.combine(middle, ranges, using=lambda mid, span: mid + width * span),
        "middle": middle,
        "lower": _core.combine(middle, ranges, using=lambda mid, span: mid - width * span),
    }


@indicator(
    category=_VOLATILITY,
    title="唐奇安通道 (Donchian Channels)",
    description=(
        "最近 period 根的最高價與最低價，中軌是兩者中點。海龜交易法的突破通道。"
        "用的是高低價而不是收盤價，所以盤中突破才抓得到。"
    ),
    result=IndicatorResult.SERIES_MAP,
    keys=("upper", "middle", "lower"),
)
def donchian_channels(highs: list[float], lows: list[float], period: int = 20) -> dict[str, Series]:
    high, low = _core.aligned(highs=highs, lows=lows)
    size = _core.period(period)
    upper = _core.rolling_max(high, size)
    lower = _core.rolling_min(low, size)
    return {
        "upper": upper,
        "middle": _core.combine(upper, lower, using=lambda top, bottom: (top + bottom) / 2),
        "lower": lower,
    }
