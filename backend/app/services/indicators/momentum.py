"""Momentum oscillators."""

from app.services.indicators import _core
from app.services.indicators._core import Series
from app.services.indicators.registry import IndicatorCategory, IndicatorResult, indicator

_MOMENTUM = IndicatorCategory.MOMENTUM


def _gains_and_losses(values: list[float]) -> tuple[Series, Series]:
    """Bar-to-bar rises and falls, both as positive numbers, None at bar 0."""
    gains = _core.blank(len(values))
    losses = _core.blank(len(values))
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains[i] = max(change, 0.0)
        losses[i] = max(-change, 0.0)
    return gains, losses


@indicator(
    category=_MOMENTUM,
    title="相對強弱指標 (RSI)",
    description=(
        "0~100 的動能指標，衡量近期上漲力道占總波動的比例。"
        "一般 70 以上視為超買、30 以下視為超賣。採用 Wilder 原始平滑法"
        "（前 period 根取簡單平均當起始值，之後 prev + (new-prev)/period），"
        "與各大看盤軟體一致；改用一般 EMA 會讓數值差到好幾點。"
    ),
)
def rsi(values: list[float], period: int = 14) -> Series:
    checked = _core.numbers(values, "values")
    size = _core.period(period)
    gains, losses = _gains_and_losses(checked)
    average_gain = _core.wilder_of(gains, size)
    average_loss = _core.wilder_of(losses, size)

    def strength(gain: float, loss: float) -> float:
        # No losses at all makes the ratio infinite; the limit is 100.
        return 100.0 if loss == 0 else 100.0 - 100.0 / (1.0 + gain / loss)

    return _core.combine(average_gain, average_loss, using=strength)


@indicator(
    category=_MOMENTUM,
    title="隨機指標 (Stochastic %K/%D)",
    description=(
        "收盤價落在最近 period 根高低區間的哪個位置，0~100。"
        "k_smooth=1 是快速隨機指標；k_smooth=3 就是常見的「慢速」(14,3,3)，"
        "此時 k 等於快速版的 d。d 是 k 的 d_period 期簡單平均。"
    ),
    result=IndicatorResult.SERIES_MAP,
    keys=("k", "d"),
)
def stochastic(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
    k_smooth: int = 1,
    d_period: int = 3,
) -> dict[str, Series]:
    high, low, close = _core.aligned(highs=highs, lows=lows, closes=closes)
    size = _core.period(period)
    smoothing = _core.period(k_smooth, "k_smooth")

    highest = _core.rolling_max(high, size)
    lowest = _core.rolling_min(low, size)
    raw = _core.blank(len(close))
    for i in range(len(close)):
        if highest[i] is None:
            continue
        span = highest[i] - lowest[i]
        # A period with no range at all: the close is by definition at both
        # ends of it, and 100 is the convention every platform uses.
        raw[i] = 100.0 if span == 0 else 100.0 * (close[i] - lowest[i]) / span

    k = raw if smoothing == 1 else _core.sma_of(raw, smoothing)
    return {"k": k, "d": _core.sma_of(k, _core.period(d_period, "d_period"))}


@indicator(
    category=_MOMENTUM,
    title="隨機相對強弱指標 (StochRSI)",
    description=(
        "把隨機指標套在 RSI 上而不是價格上，因此比 RSI 敏感得多，常用來抓 RSI 自己的超買超賣。"
        "stoch_rsi 是未平滑的原始值，k 是它的 k_smooth 期平均，d 再平均一次。"
    ),
    result=IndicatorResult.SERIES_MAP,
    keys=("stoch_rsi", "k", "d"),
)
def stoch_rsi(
    values: list[float],
    rsi_period: int = 14,
    stoch_period: int = 14,
    k_smooth: int = 3,
    d_period: int = 3,
) -> dict[str, Series]:
    strength = rsi(values, _core.period(rsi_period, "rsi_period"))
    size = _core.period(stoch_period, "stoch_period")
    start = _core.first_value_index(strength)
    settled = [value for value in strength[start:] if value is not None]

    raw = _core.blank(len(strength))
    for offset in range(size - 1, len(settled)):
        window = settled[offset - size + 1 : offset + 1]
        span = max(window) - min(window)
        raw[start + offset] = 100.0 if span == 0 else 100.0 * (window[-1] - min(window)) / span

    smoothing = _core.period(k_smooth, "k_smooth")
    k = raw if smoothing == 1 else _core.sma_of(raw, smoothing)
    return {
        "stoch_rsi": raw,
        "k": k,
        "d": _core.sma_of(k, _core.period(d_period, "d_period")),
    }


@indicator(
    category=_MOMENTUM,
    title="順勢指標 (CCI)",
    description=(
        "典型價格偏離其均值多少個「平均絕對偏差」，慣例 +100 以上為強勢、-100 以下為弱勢。"
        "分母用的是平均絕對偏差乘上 0.015，不是標準差──換成標準差會讓 ±100 這兩條線失去意義。"
    ),
)
def cci(highs: list[float], lows: list[float], closes: list[float], period: int = 20) -> Series:
    high, low, close = _core.aligned(highs=highs, lows=lows, closes=closes)
    size = _core.period(period)
    typical = _core.typical_prices(high, low, close)

    out = _core.blank(len(close))
    for i in range(size - 1, len(close)):
        window = typical[i - size + 1 : i + 1]
        mean = sum(window) / size
        deviation = sum(abs(value - mean) for value in window) / size
        out[i] = 0.0 if deviation == 0 else (typical[i] - mean) / (0.015 * deviation)
    return out


@indicator(
    category=_MOMENTUM,
    title="威廉指標 (Williams %R)",
    description="與隨機指標同一件事但由上往下量，範圍 -100~0：-20 以上為超買、-80 以下為超賣。",
)
def williams_r(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> Series:
    high, low, close = _core.aligned(highs=highs, lows=lows, closes=closes)
    size = _core.period(period)
    highest = _core.rolling_max(high, size)
    lowest = _core.rolling_min(low, size)

    out = _core.blank(len(close))
    for i in range(len(close)):
        if highest[i] is None:
            continue
        span = highest[i] - lowest[i]
        out[i] = -100.0 if span == 0 else -100.0 * (highest[i] - close[i]) / span
    return out


@indicator(
    category=_MOMENTUM,
    title="變動率 (ROC)",
    description=(
        "相對 period 根之前的漲跌「百分比」。"
        "注意與 momentum 的差別：ROC 有除以舊價格，跨標的可比。"
    ),
)
def roc(values: list[float], period: int = 12) -> Series:
    checked = _core.numbers(values, "values")
    size = _core.period(period)
    out = _core.blank(len(checked))
    for i in range(size, len(checked)):
        previous = checked[i - size]
        if previous:
            out[i] = 100.0 * (checked[i] / previous - 1.0)
    return out


@indicator(
    category=_MOMENTUM,
    title="動量 (Momentum)",
    description=(
        "相對 period 根之前的漲跌「絕對值」，單位就是價格單位。"
        "門檻不能跨標的沿用（見 ROC）。"
    ),
)
def momentum(values: list[float], period: int = 10) -> Series:
    checked = _core.numbers(values, "values")
    size = _core.period(period)
    return [None if i < size else checked[i] - checked[i - size] for i in range(len(checked))]


@indicator(
    category=_MOMENTUM,
    title="資金流量指標 (MFI)",
    description=(
        "把成交量算進去的 RSI：用典型價格判斷流入或流出，再用成交金額加權，0~100。"
        "常被稱為「量價版 RSI」，價量背離時與 RSI 分道揚鑣。"
    ),
)
def mfi(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    period: int = 14,
) -> Series:
    high, low, close, volume = _core.aligned(
        highs=highs, lows=lows, closes=closes, volumes=volumes
    )
    size = _core.period(period)
    typical = _core.typical_prices(high, low, close)

    positive = _core.blank(len(close))
    negative = _core.blank(len(close))
    for i in range(1, len(close)):
        flow = typical[i] * volume[i]
        # An unchanged typical price counts as neither, which is why this is
        # not simply "up bar / down bar".
        positive[i] = flow if typical[i] > typical[i - 1] else 0.0
        negative[i] = flow if typical[i] < typical[i - 1] else 0.0

    def index(up: float, down: float) -> float:
        return 100.0 if down == 0 else 100.0 - 100.0 / (1 + up / down)

    # Bar 0 has no previous typical price to compare against, so the sums
    # start at bar 1 and the result is padded back to full length.
    ratio = _core.combine(
        _core.rolling_sum(positive[1:], size),
        _core.rolling_sum(negative[1:], size),
        using=index,
    )
    return _core.prepend_gap(ratio, len(close))


@indicator(
    category=_MOMENTUM,
    title="真實強弱指標 (TSI)",
    description=(
        "對每根漲跌幅做兩次 EMA 平滑（先 long_period 再 short_period），"
        "再除以同樣平滑過的漲跌絕對值，得到 -100~100 的平滑動能。"
        "預設 25/13 需要約 37 根K線才有值。"
    ),
    result=IndicatorResult.SERIES_MAP,
    keys=("tsi", "signal"),
)
def tsi(
    values: list[float], long_period: int = 25, short_period: int = 13, signal_period: int = 13
) -> dict[str, Series]:
    checked = _core.numbers(values, "values")
    slow = _core.period(long_period, "long_period")
    fast = _core.period(short_period, "short_period")

    changes = _core.blank(len(checked))
    magnitudes = _core.blank(len(checked))
    for i in range(1, len(checked)):
        changes[i] = checked[i] - checked[i - 1]
        magnitudes[i] = abs(changes[i])

    smoothed = _core.ema_of(_core.ema_of(changes, slow), fast)
    scale = _core.ema_of(_core.ema_of(magnitudes, slow), fast)
    line = _core.combine(
        smoothed, scale, using=lambda value, size: 0.0 if size == 0 else 100.0 * value / size
    )
    return {"tsi": line, "signal": _core.ema_of(line, _core.period(signal_period, "signal_period"))}


@indicator(
    category=_MOMENTUM,
    title="終極擺盪指標 (Ultimate Oscillator)",
    description=(
        "同時看短、中、長三個週期的買盤壓力，權重 4:2:1，0~100。"
        "設計目的是減少單一週期造成的假背離。"
    ),
)
def ultimate_oscillator(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    short_period: int = 7,
    medium_period: int = 14,
    long_period: int = 28,
) -> Series:
    high, low, close = _core.aligned(highs=highs, lows=lows, closes=closes)
    short = _core.period(short_period, "short_period")
    medium = _core.period(medium_period, "medium_period")
    long = _core.period(long_period, "long_period")

    buying_pressure = []
    true_range = []
    for i in range(1, len(close)):
        floor = min(low[i], close[i - 1])
        buying_pressure.append(close[i] - floor)
        true_range.append(max(high[i], close[i - 1]) - floor)

    def average(size: int) -> Series:
        return _core.combine(
            _core.rolling_sum(buying_pressure, size),
            _core.rolling_sum(true_range, size),
            using=lambda pressure, span: None if span == 0 else pressure / span,
        )

    weighted = _core.combine(
        average(short),
        average(medium),
        average(long),
        using=lambda a, b, c: 100.0 * (4 * a + 2 * b + c) / 7.0,
    )
    return _core.prepend_gap(weighted, len(close))


@indicator(
    category=_MOMENTUM,
    title="錢德動量擺盪指標 (CMO)",
    description=(
        "(上漲總和 - 下跌總和) / (上漲總和 + 下跌總和) * 100，範圍 -100~100。"
        "用的是單純加總而非 Wilder 平滑，因此數值上恰好等於同樣算法的 RSI 乘二減一百。"
    ),
)
def cmo(values: list[float], period: int = 14) -> Series:
    checked = _core.numbers(values, "values")
    size = _core.period(period)
    gains, losses = _gains_and_losses(checked)

    def spread(up: float, down: float) -> float:
        return 0.0 if up + down == 0 else 100.0 * (up - down) / (up + down)

    ratio = _core.combine(
        _core.rolling_sum(gains[1:], size), _core.rolling_sum(losses[1:], size), using=spread
    )
    return _core.prepend_gap(ratio, len(checked))
