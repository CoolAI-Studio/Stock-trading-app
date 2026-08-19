"""Trend indicators: moving averages and direction.

Conventions (alignment, seeding, plain lists) are in _core.py; nothing here
restates them.
"""

import math

from app.services.indicators import _core
from app.services.indicators._core import Series
from app.services.indicators.registry import IndicatorCategory, IndicatorResult, indicator

_TREND = IndicatorCategory.TREND


@indicator(
    category=_TREND,
    title="簡單移動平均 (SMA)",
    description=(
        "最近 period 根K線收盤價的算術平均，最基本的趨勢線。價格站上或跌破均線常被當成多空分界。"
    ),
)
def sma(values: list[float], period: int = 20) -> Series:
    return _core.sma_values(_core.numbers(values, "values"), _core.period(period))


@indicator(
    category=_TREND,
    title="指數移動平均 (EMA)",
    description=(
        "指數加權移動平均，越近期的價格權重越高，因此比 SMA 更快反應轉折。"
        "起始值採用前 period 根的簡單平均（與 TA-Lib、TradingView 相同）。"
    ),
)
def ema(values: list[float], period: int = 20) -> Series:
    return _core.ema_values(_core.numbers(values, "values"), _core.period(period))


@indicator(
    category=_TREND,
    title="加權移動平均 (WMA)",
    description=(
        "線性加權平均：窗口內第 1 根權重 1、最後一根權重 period，比 SMA 靈敏、比 EMA 穩定。"
    ),
)
def wma(values: list[float], period: int = 20) -> Series:
    return _core.wma_values(_core.numbers(values, "values"), _core.period(period))


@indicator(
    category=_TREND,
    title="雙重指數移動平均 (DEMA)",
    description=(
        "2*EMA - EMA(EMA)，扣掉一層 EMA 的落後，轉折比 EMA 早但雜訊也較多。"
        "需要約 2*period 根K線才有值。"
    ),
)
def dema(values: list[float], period: int = 20) -> Series:
    checked = _core.numbers(values, "values")
    size = _core.period(period)
    single = _core.ema_values(checked, size)
    double = _core.ema_of(single, size)
    return _core.combine(single, double, using=lambda one, two: 2 * one - two)


@indicator(
    category=_TREND,
    title="三重指數移動平均 (TEMA)",
    description=(
        "3*EMA - 3*EMA(EMA) + EMA(EMA(EMA))，比 DEMA 再少一層落後。需要約 3*period 根K線才有值。"
    ),
)
def tema(values: list[float], period: int = 20) -> Series:
    checked = _core.numbers(values, "values")
    size = _core.period(period)
    single = _core.ema_values(checked, size)
    double = _core.ema_of(single, size)
    triple = _core.ema_of(double, size)
    return _core.combine(
        single, double, triple, using=lambda one, two, three: 3 * one - 3 * two + three
    )


@indicator(
    category=_TREND,
    title="赫爾移動平均 (HMA)",
    description=(
        "WMA(2*WMA(period/2) - WMA(period), sqrt(period))。"
        "設計目的是同時做到「貼近價格」與「線條平滑」，常用來抓趨勢方向而非進出點。"
    ),
)
def hma(values: list[float], period: int = 9) -> Series:
    checked = _core.numbers(values, "values")
    size = _core.period(period, minimum=2)
    half = _core.wma_values(checked, max(size // 2, 1))
    full = _core.wma_values(checked, size)
    raw = _core.combine(half, full, using=lambda h, f: 2 * h - f)
    return _core.wma_of(raw, max(int(math.sqrt(size)), 1))


@indicator(
    category=_TREND,
    title="成交量加權移動平均 (VWMA)",
    description=(
        "用成交量當權重的移動平均：放量的K線影響大、量縮的K線影響小，比 SMA 更貼近實際成交成本。"
    ),
)
def vwma(values: list[float], volumes: list[float], period: int = 20) -> Series:
    prices, weights = _core.aligned(values=values, volumes=volumes)
    size = _core.period(period)
    weighted = [price * weight for price, weight in zip(prices, weights, strict=True)]
    return _core.combine(
        _core.rolling_sum(weighted, size),
        _core.rolling_sum(weights, size),
        using=lambda top, bottom: top / bottom if bottom else None,
    )


@indicator(
    category=_TREND,
    title="考夫曼適應性均線 (KAMA)",
    description=(
        "依「效率比」自動調整快慢：單邊行情時貼著價格跑，盤整時幾乎不動，"
        "用來過濾假突破。fast_period/slow_period 是兩端的 EMA 長度。"
    ),
)
def kama(
    values: list[float], period: int = 10, fast_period: int = 2, slow_period: int = 30
) -> Series:
    checked = _core.numbers(values, "values")
    size = _core.period(period)
    fastest = 2.0 / (_core.period(fast_period) + 1)
    slowest = 2.0 / (_core.period(slow_period) + 1)
    if len(checked) < size:
        return _core.blank(len(checked))

    out = _core.blank(len(checked))
    current = sum(checked[:size]) / size
    out[size - 1] = current
    # Pairwise over consecutive bars, so the two arms are deliberately
    # different lengths -- strict=True would raise on every call.
    steps = [abs(b - a) for a, b in zip(checked, checked[1:], strict=False)]
    for i in range(size, len(checked)):
        # Efficiency ratio: net travel over total travel. 1 means a straight
        # line, 0 means the price came back to where it started.
        noise = sum(steps[i - size : i])
        ratio = 0.0 if noise == 0 else abs(checked[i] - checked[i - size]) / noise
        smoothing = (ratio * (fastest - slowest) + slowest) ** 2
        current += smoothing * (checked[i] - current)
        out[i] = current
    return out


@indicator(
    category=_TREND,
    title="指數平滑異同移動平均 (MACD)",
    description=(
        "macd 是快線（EMA(fast_period) - EMA(slow_period)），signal 是慢線"
        "（macd 自己的 EMA(signal_period)），histogram 是兩者之差。"
        "快線由上往下穿越慢線＝死亡交叉，反之為黃金交叉。"
        "慢線比快線晚 signal_period-1 根才有值，這段期間 signal 是 None。"
    ),
    result=IndicatorResult.SERIES_MAP,
    keys=("macd", "signal", "histogram"),
)
def macd(
    values: list[float], fast_period: int = 12, slow_period: int = 26, signal_period: int = 9
) -> dict[str, Series]:
    checked = _core.numbers(values, "values")
    fast = _core.ema_values(checked, _core.period(fast_period, "fast_period"))
    slow = _core.ema_values(checked, _core.period(slow_period, "slow_period"))
    line = _core.combine(fast, slow, using=lambda f, s: f - s)
    signal = _core.ema_of(line, _core.period(signal_period, "signal_period"))
    return {
        "macd": line,
        "signal": signal,
        "histogram": _core.combine(line, signal, using=lambda m, s: m - s),
    }


@indicator(
    category=_TREND,
    title="平均趨向指標 (ADX/DMI)",
    description=(
        "plus_di 與 minus_di 分別衡量上升與下降的方向力道，adx 衡量「趨勢有多強」但不分多空。"
        "adx 通常 25 以上視為有趨勢、20 以下視為盤整。全部採用 Wilder 平滑，"
        "adx 需要約 2*period 根K線才會有值。"
    ),
    result=IndicatorResult.SERIES_MAP,
    keys=("plus_di", "minus_di", "adx"),
)
def adx(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> dict[str, Series]:
    high, low, close = _core.aligned(highs=highs, lows=lows, closes=closes)
    size = _core.period(period)
    length = len(close)

    plus_moves = _core.blank(length)
    minus_moves = _core.blank(length)
    for i in range(1, length):
        up = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        # Only the larger of the two counts, and only if it is positive: an
        # inside bar contributes no directional movement at all.
        plus_moves[i] = up if up > down and up > 0 else 0.0
        minus_moves[i] = down if down > up and down > 0 else 0.0

    smoothed_range = _core.wilder_of(_core.true_ranges(high, low, close), size)
    plus_di = _core.combine(
        _core.wilder_of(plus_moves, size),
        smoothed_range,
        using=lambda move, span: 100.0 * move / span if span else 0.0,
    )
    minus_di = _core.combine(
        _core.wilder_of(minus_moves, size),
        smoothed_range,
        using=lambda move, span: 100.0 * move / span if span else 0.0,
    )
    directional_index = _core.combine(
        plus_di,
        minus_di,
        using=lambda up, down: 0.0 if up + down == 0 else 100.0 * abs(up - down) / (up + down),
    )
    return {
        "plus_di": plus_di,
        "minus_di": minus_di,
        "adx": _core.wilder_of(directional_index, size),
    }


@indicator(
    category=_TREND,
    title="阿隆指標 (Aroon)",
    description=(
        "up 表示「距離最近一次 period 期新高過了多久」，100 代表就是今天創高；down 同理看新低。"
        "oscillator 是 up - down。窗口含當根共 period+1 根K線（與 TA-Lib、TradingView 相同）。"
    ),
    result=IndicatorResult.SERIES_MAP,
    keys=("up", "down", "oscillator"),
)
def aroon(highs: list[float], lows: list[float], period: int = 25) -> dict[str, Series]:
    high, low = _core.aligned(highs=highs, lows=lows)
    size = _core.period(period)
    length = len(high)

    up = _core.blank(length)
    down = _core.blank(length)
    for i in range(size, length):
        window_high = high[i - size : i + 1]
        window_low = low[i - size : i + 1]
        # Ties resolve to the most recent bar, which is what "days since the
        # high" means when the high has just been matched.
        since_high = size - max(range(size + 1), key=lambda k: (window_high[k], k))
        since_low = size - min(range(size + 1), key=lambda k: (window_low[k], -k))
        up[i] = 100.0 * (size - since_high) / size
        down[i] = 100.0 * (size - since_low) / size
    return {
        "up": up,
        "down": down,
        "oscillator": _core.combine(up, down, using=lambda u, d: u - d),
    }


def _midpoint(highs: list[float], lows: list[float], size: int) -> Series:
    return _core.combine(
        _core.rolling_max(highs, size),
        _core.rolling_min(lows, size),
        using=lambda top, bottom: (top + bottom) / 2,
    )


@indicator(
    category=_TREND,
    title="一目均衡表 (Ichimoku)",
    description=(
        "conversion 轉換線、base 基準線、span_a/span_b 構成雲層、lagging 遲行線。"
        "span_a/span_b 已經照 displacement 往「前」位移，也就是回傳值就是圖上當根該看到的雲；"
        "lagging 是往「後」位移的收盤價，所以最後 displacement 根必然是 None"
        "（那幾根的收盤價還沒發生）。"
    ),
    result=IndicatorResult.SERIES_MAP,
    keys=("conversion", "base", "span_a", "span_b", "lagging"),
)
def ichimoku(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    conversion_period: int = 9,
    base_period: int = 26,
    span_b_period: int = 52,
    displacement: int = 26,
) -> dict[str, Series]:
    high, low, close = _core.aligned(highs=highs, lows=lows, closes=closes)
    length = len(close)
    shift = _core.period(displacement, "displacement")

    conversion = _midpoint(high, low, _core.period(conversion_period, "conversion_period"))
    base = _midpoint(high, low, _core.period(base_period, "base_period"))
    span_a_raw = _core.combine(conversion, base, using=lambda c, b: (c + b) / 2)
    span_b_raw = _midpoint(high, low, _core.period(span_b_period, "span_b_period"))

    def forward(series: Series) -> Series:
        return [series[i - shift] if i >= shift else None for i in range(length)]

    return {
        "conversion": conversion,
        "base": base,
        "span_a": forward(span_a_raw),
        "span_b": forward(span_b_raw),
        "lagging": [close[i + shift] if i + shift < length else None for i in range(length)],
    }


@indicator(
    category=_TREND,
    title="拋物線轉向 (Parabolic SAR)",
    description=(
        "停損轉向點：上升趨勢中位於價格下方、下降趨勢中位於價格上方，價格觸及即反轉。"
        "step 是加速因子的每次增量、max_step 是上限。第 0 根沒有值。"
    ),
)
def parabolic_sar(
    highs: list[float], lows: list[float], step: float = 0.02, max_step: float = 0.2
) -> Series:
    high, low = _core.aligned(highs=highs, lows=lows)
    increment = _core.multiplier(step, "step")
    ceiling = _core.multiplier(max_step, "max_step")
    length = len(high)
    if length < 2:
        return _core.blank(length)

    out = _core.blank(length)
    # Wilder starts from whichever way the first two bars leaned; a wrong
    # guess costs one reversal and then self-corrects.
    rising = high[1] + low[1] >= high[0] + low[0]
    extreme = high[1] if rising else low[1]
    out[1] = min(low[0], low[1]) if rising else max(high[0], high[1])
    acceleration = increment

    for i in range(2, length):
        value = out[i - 1] + acceleration * (extreme - out[i - 1])
        if rising:
            # The SAR may never move inside the last two bars' range, or it
            # would stop out on a bar that has already been survived.
            value = min(value, low[i - 1], low[i - 2])
            if low[i] < value:
                rising, value, extreme, acceleration = False, extreme, low[i], increment
            elif high[i] > extreme:
                extreme, acceleration = high[i], min(acceleration + increment, ceiling)
        else:
            value = max(value, high[i - 1], high[i - 2])
            if high[i] > value:
                rising, value, extreme, acceleration = True, extreme, high[i], increment
            elif low[i] < extreme:
                extreme, acceleration = low[i], min(acceleration + increment, ceiling)
        out[i] = value
    return out


@indicator(
    category=_TREND,
    title="超級趨勢 (SuperTrend)",
    description=(
        "以 ATR 為寬度的追蹤停損線。direction 為 1 代表多頭（線在價格下方）、"
        "-1 代表空頭（線在上方），"
        "direction 翻轉就是進出訊號。upper/lower 是計算過程中的上下軌。"
    ),
    result=IndicatorResult.SERIES_MAP,
    keys=("supertrend", "direction", "upper", "lower"),
)
def supertrend(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 10,
    multiplier: float = 3.0,
) -> dict[str, Series]:
    high, low, close = _core.aligned(highs=highs, lows=lows, closes=closes)
    width = _core.multiplier(multiplier, "multiplier")
    ranges = _core.wilder_of(_core.true_ranges(high, low, close), _core.period(period))
    length = len(close)

    line = _core.blank(length)
    direction = _core.blank(length)
    upper = _core.blank(length)
    lower = _core.blank(length)

    for i in range(length):
        if ranges[i] is None:
            continue
        middle = (high[i] + low[i]) / 2
        basic_upper = middle + width * ranges[i]
        basic_lower = middle - width * ranges[i]

        if upper[i - 1] is None:
            upper[i], lower[i] = basic_upper, basic_lower
            direction[i] = 1 if close[i] > basic_upper else -1
        else:
            # Each band only ever tightens, until price closes through it and
            # releases it. Recomputing from scratch every bar would make the
            # line jitter and produce reversals that never happened.
            upper[i] = (
                basic_upper
                if basic_upper < upper[i - 1] or close[i - 1] > upper[i - 1]
                else upper[i - 1]
            )
            lower[i] = (
                basic_lower
                if basic_lower > lower[i - 1] or close[i - 1] < lower[i - 1]
                else lower[i - 1]
            )
            if direction[i - 1] == -1:
                direction[i] = 1 if close[i] > upper[i] else -1
            else:
                direction[i] = -1 if close[i] < lower[i] else 1
        line[i] = lower[i] if direction[i] == 1 else upper[i]

    return {"supertrend": line, "direction": direction, "upper": upper, "lower": lower}


@indicator(
    category=_TREND,
    title="三重指數平滑均線變動率 (TRIX)",
    description=(
        "對價格做三次 EMA 平滑後取變動率（百分比），幾乎濾掉所有短週期雜訊。"
        "trix 穿越 0 軸或穿越 signal 線視為多空轉換。"
    ),
    result=IndicatorResult.SERIES_MAP,
    keys=("trix", "signal"),
)
def trix(values: list[float], period: int = 15, signal_period: int = 9) -> dict[str, Series]:
    checked = _core.numbers(values, "values")
    size = _core.period(period)
    smoothed = _core.ema_of(_core.ema_of(_core.ema_values(checked, size), size), size)

    line = _core.blank(len(checked))
    for i in range(1, len(checked)):
        previous = smoothed[i - 1]
        if smoothed[i] is not None and previous:
            line[i] = 100.0 * (smoothed[i] / previous - 1.0)
    smoothing = _core.period(signal_period, "signal_period")
    return {"trix": line, "signal": _core.ema_of(line, smoothing)}
