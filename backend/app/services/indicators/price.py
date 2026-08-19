"""Price transforms: different ways of reading the same candles."""

from app.services.indicators import _core
from app.services.indicators._core import Series
from app.services.indicators.registry import IndicatorCategory, IndicatorResult, indicator

_PRICE = IndicatorCategory.PRICE

_PIVOT_METHODS = ("classic", "fibonacci")


@indicator(
    category=_PRICE,
    title="典型價格 (Typical Price)",
    description=(
        "(最高 + 最低 + 收盤) / 3，CCI、MFI、VWAP 的計算基礎，比單看收盤價更能代表當根的成交區間。"
    ),
)
def typical_price(highs: list[float], lows: list[float], closes: list[float]) -> Series:
    high, low, close = _core.aligned(highs=highs, lows=lows, closes=closes)
    return _core.typical_prices(high, low, close)


@indicator(
    category=_PRICE,
    title="平均K線 (Heikin Ashi)",
    description=(
        "把原始K線改畫成平滑K線：close 是當根 OHLC 的平均，open 是「前一根平均K線」的開收中點，"
        "high/low 再把合成的實體包進去。連續同色代表趨勢延續，常用來過濾雜訊。"
        "注意 open 是遞迴的──用原始 open 代替就完全失去平滑效果。"
    ),
    result=IndicatorResult.SERIES_MAP,
    keys=("open", "high", "low", "close"),
)
def heikin_ashi(
    opens: list[float], highs: list[float], lows: list[float], closes: list[float]
) -> dict[str, Series]:
    open_, high, low, close = _core.aligned(opens=opens, highs=highs, lows=lows, closes=closes)
    if not close:
        return {"open": [], "high": [], "low": [], "close": []}

    ha_close = [(o + h + lo + c) / 4 for o, h, lo, c in zip(open_, high, low, close, strict=True)]
    # The first candle has no predecessor to average, so it seeds from the
    # real bar; every later one is built on the synthetic candle before it.
    ha_open = [(open_[0] + close[0]) / 2]
    for i in range(1, len(close)):
        ha_open.append((ha_open[i - 1] + ha_close[i - 1]) / 2)

    return {
        "open": list(ha_open),
        "high": [max(high[i], ha_open[i], ha_close[i]) for i in range(len(close))],
        "low": [min(low[i], ha_open[i], ha_close[i]) for i in range(len(close))],
        "close": list(ha_close),
    }


@indicator(
    category=_PRICE,
    title="樞紐點 (Pivot Points)",
    description=(
        "用「上一個週期」的高、低、收盤價算出當期的支撐壓力位，回傳 p 與 r1~r3、s1~s3 七個價位。"
        "因此參數是三個數字而不是三條序列──例如要算今天的日內樞紐，就傳昨天的高低收。"
        "method 可選 classic（傳統）或 fibonacci（費波納契回撤）。"
    ),
    result=IndicatorResult.VALUE_MAP,
    keys=("p", "r1", "r2", "r3", "s1", "s2", "s3"),
)
def pivot_points(
    high: float, low: float, close: float, method: str = "classic"
) -> dict[str, float]:
    top = _core.multiplier(high, "high")
    bottom = _core.multiplier(low, "low")
    last = _core.multiplier(close, "close")
    if method not in _PIVOT_METHODS:
        raise ValueError(f"method must be one of {', '.join(_PIVOT_METHODS)}, not {method!r}.")

    pivot = (top + bottom + last) / 3
    span = top - bottom
    if method == "classic":
        return {
            "p": pivot,
            "r1": 2 * pivot - bottom,
            "r2": pivot + span,
            "r3": top + 2 * (pivot - bottom),
            "s1": 2 * pivot - top,
            "s2": pivot - span,
            "s3": bottom - 2 * (top - pivot),
        }
    return {
        "p": pivot,
        "r1": pivot + 0.382 * span,
        "r2": pivot + 0.618 * span,
        "r3": pivot + span,
        "s1": pivot - 0.382 * span,
        "s2": pivot - 0.618 * span,
        "s3": pivot - span,
    }
