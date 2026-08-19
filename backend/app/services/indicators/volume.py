"""Volume indicators."""

from app.services.indicators import _core
from app.services.indicators._core import Series
from app.services.indicators.registry import IndicatorCategory, indicator

_VOLUME = IndicatorCategory.VOLUME


def _money_flow_volume(
    highs: list[float], lows: list[float], closes: list[float], volumes: list[float]
) -> list[float]:
    """Volume signed by where in the bar it closed.

    A bar with no range at all -- a limit-up day, which Taiwan equities have
    often enough to matter -- would divide by zero, and contributes nothing
    rather than crashing the strategy that asked for it.
    """
    out = []
    for high, low, close, volume in zip(highs, lows, closes, volumes, strict=True):
        span = high - low
        multiplier = 0.0 if span == 0 else ((close - low) - (high - close)) / span
        out.append(multiplier * volume)
    return out


@indicator(
    category=_VOLUME,
    title="能量潮 (OBV)",
    description=(
        "收盤上漲就加上當根成交量、下跌就減掉，持平則不動，累加而成。"
        "數列從 0 起算（與 Pine 的 ta.obv 相同），所以有意義的是它的方向與背離，絕對值沒有意義。"
    ),
)
def obv(closes: list[float], volumes: list[float]) -> Series:
    close, volume = _core.aligned(closes=closes, volumes=volumes)
    if not close:
        return []
    out = [0.0]
    for i in range(1, len(close)):
        if close[i] > close[i - 1]:
            out.append(out[-1] + volume[i])
        elif close[i] < close[i - 1]:
            out.append(out[-1] - volume[i])
        else:
            out.append(out[-1])
    return out


@indicator(
    category=_VOLUME,
    title="成交量加權平均價 (VWAP)",
    description=(
        "以成交量加權的典型價格平均，也就是「平均成交成本」。"
        "period 留空是從傳入序列的第一根累計到當根（要盤中 VWAP 就只傳當日K線）；"
        "給 period 則變成滾動視窗。"
    ),
)
def vwap(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    period: int | None = None,
) -> Series:
    high, low, close, volume = _core.aligned(highs=highs, lows=lows, closes=closes, volumes=volumes)
    typical = _core.typical_prices(high, low, close)
    weighted = [price * size for price, size in zip(typical, volume, strict=True)]

    if period is not None:
        window = _core.period(period)
        return _core.combine(
            _core.rolling_sum(weighted, window),
            _core.rolling_sum(volume, window),
            using=lambda money, size: None if size == 0 else money / size,
        )

    out = _core.blank(len(close))
    money = 0.0
    traded = 0.0
    for i in range(len(close)):
        money += weighted[i]
        traded += volume[i]
        out[i] = None if traded == 0 else money / traded
    return out


@indicator(
    category=_VOLUME,
    title="累積/派發線 (A/D Line)",
    description=(
        "每根K線依收盤價在當根高低區間的位置，把成交量分成買方或賣方，再累加。"
        "收在最高價就整根算買方，收在中點則相互抵銷。與 OBV 一樣只看方向與背離。"
    ),
)
def accumulation_distribution(
    highs: list[float], lows: list[float], closes: list[float], volumes: list[float]
) -> Series:
    high, low, close, volume = _core.aligned(highs=highs, lows=lows, closes=closes, volumes=volumes)
    out: Series = []
    running = 0.0
    for flow in _money_flow_volume(high, low, close, volume):
        running += flow
        out.append(running)
    return out


@indicator(
    category=_VOLUME,
    title="蔡金資金流量 (CMF)",
    description=(
        "A/D 的分子在 period 期內的總和，除以同期成交量總和，範圍 -1~1。"
        "持續為正代表買盤在吸收，持續為負代表賣壓在出貨。"
    ),
)
def chaikin_money_flow(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    period: int = 20,
) -> Series:
    high, low, close, volume = _core.aligned(highs=highs, lows=lows, closes=closes, volumes=volumes)
    size = _core.period(period)
    return _core.combine(
        _core.rolling_sum(_money_flow_volume(high, low, close, volume), size),
        _core.rolling_sum(volume, size),
        using=lambda flow, traded: None if traded == 0 else flow / traded,
    )


@indicator(
    category=_VOLUME,
    title="勁道指數 (Force Index)",
    description=(
        "(收盤 - 前收盤) * 成交量，再取 period 期 EMA。"
        "同時衡量漲跌幅與參與量，因此可以區分「量縮的上漲」與「帶量的上漲」。"
        "period=1 就是未平滑的原始值。第 0 根沒有前收盤價，所以沒有值。"
    ),
)
def force_index(closes: list[float], volumes: list[float], period: int = 13) -> Series:
    close, volume = _core.aligned(closes=closes, volumes=volumes)
    size = _core.period(period)
    raw = _core.blank(len(close))
    for i in range(1, len(close)):
        raw[i] = (close[i] - close[i - 1]) * volume[i]
    return raw if size == 1 else _core.ema_of(raw, size)


@indicator(
    category=_VOLUME,
    title="成交量擺盪指標 (Volume Oscillator)",
    description=(
        "短期與長期成交量 EMA 的差，換算成長期 EMA 的百分比。"
        "為正代表近期成交量高於長期水準，常用來確認突破是否有量。"
    ),
)
def volume_oscillator(volumes: list[float], short_period: int = 5, long_period: int = 10) -> Series:
    volume = _core.numbers(volumes, "volumes")
    short = _core.ema_values(volume, _core.period(short_period, "short_period"))
    long = _core.ema_values(volume, _core.period(long_period, "long_period"))
    return _core.combine(
        short, long, using=lambda fast, slow: None if slow == 0 else 100.0 * (fast - slow) / slow
    )
