"""Which axis each indicator output belongs on.

A moving average shares the price axis with the candles. RSI runs 0-100 and OBV
runs to ±7.6e7 -- put either of those on the price axis and the candles collapse
into a flat line at the bottom of the chart. So every output needs an answer,
and the answer cannot be derived:

  NOT from `spec.category`. `trend` holds both sma (price axis) and macd
  (±3); `volume` holds vwap (price axis) alongside obv (±7.6e7).

  NOT from `spec.result`. sma and rsi are both plain series and belong on
  different axes.

  NOT from the value range. atr (1.6-2.2) and stdev (0.7-5.2) are in price
  UNITS but are not price LEVELS -- anything that reads 「comparable to price」
  off a number sends them to the price axis and pins them to the floor.

So it is a declared fact, written down once, and a test fails if the catalogue
ever grows an entry this file has not answered for. That test is the enforcement:
without it the 41st indicator lands on the price axis silently, and a squashed
chart is not an error anybody sees as one.

TWO INDICATORS SPLIT ACROSS AXES within a single call, which is why the map is
keyed per OUTPUT and not per indicator:
  bollinger_bands -- upper/middle/lower are prices, bandwidth is ~4-26 and
    percent_b is ~0-1.
  supertrend -- the three lines are prices, direction is ±1.

Ranges quoted above are measured over a 300-bar synthetic walk in the 68-101
band; they are there to show the ORDER of magnitude, not as thresholds.
"""

from app.services.indicators import INDICATOR_CATEGORIES, catalogue

PRICE = "price"
OWN = "own"

# Indicators every one of whose outputs shares the candles' axis.
_PRICE_PANE = frozenset(
    {
        "dema",
        "ema",
        "hma",
        "ichimoku",
        "kama",
        "parabolic_sar",
        "sma",
        "tema",
        "vwma",
        "wma",
        "donchian_channels",
        "keltner_channels",
        "heikin_ashi",
        "typical_price",
        "vwap",
    }
)

# Indicators every one of whose outputs needs its own axis.
_OWN_PANE = frozenset(
    {
        "adx",
        "aroon",
        "macd",
        "trix",
        "cci",
        "cmo",
        "mfi",
        "momentum",
        "roc",
        "rsi",
        "stoch_rsi",
        "stochastic",
        "tsi",
        "ultimate_oscillator",
        "williams_r",
        # In price UNITS, not price LEVELS. A 2-dollar average true range drawn
        # against a 100-dollar stock is a line on the floor.
        "atr",
        "stdev",
        "accumulation_distribution",
        "chaikin_money_flow",
        "force_index",
        "obv",
        "volume_oscillator",
    }
)

# The two that mix scales inside one call.
_SPLIT: dict[str, dict[str, str]] = {
    "bollinger_bands": {
        "upper": PRICE,
        "middle": PRICE,
        "lower": PRICE,
        "bandwidth": OWN,
        "percent_b": OWN,
    },
    "supertrend": {
        "supertrend": PRICE,
        "upper": PRICE,
        "lower": PRICE,
        # ±1, and never 0. Drawn on the price axis it is a square wave at the
        # very bottom; it is a colour, not a line.
        "direction": OWN,
    },
}

# Cannot be drawn at all. pivot_points takes three SCALARS (high/low/close) and
# returns seven scalars -- there is no series for a line renderer to draw, and
# every other indicator in the catalogue takes series. Excluded structurally
# rather than by name-matching somewhere downstream, so the chart never offers
# a choice that cannot work.
UNCHARTABLE = frozenset({"pivot_points"})


def pane_for(name: str, key: str = "") -> str:
    """PRICE or OWN. Raises for anything this file has not answered for.

    Raising rather than defaulting: a default of PRICE squashes the chart and a
    default of OWN buries a moving average in a strip below it, and both are
    silent. An unanswered indicator is a gap in this file, and it should be
    impossible to ship one.
    """
    split = _SPLIT.get(name)
    if split is not None:
        if key not in split:
            raise KeyError(f"{name}.{key} has no declared pane")
        return split[key]
    if name in _PRICE_PANE:
        return PRICE
    if name in _OWN_PANE:
        return OWN
    raise KeyError(f"{name} has no declared pane")


# The outputs that share a pane but NOT an axis.
#
# Measured over a 300-bar walk, across every multi-output oscillator in the
# catalogue: adx, aroon, macd, trix, stoch_rsi, stochastic and tsi all have
# outputs on comparable scales, and they are meant to be read against each
# other -- macd against its own signal line is the entire point of macd.
# Separating those would destroy the comparison.
#
# bollinger_bands is the one exception: bandwidth runs 4.5-25 while percent_b
# runs -0.2-1.2, so on a shared axis percent_b is a flat line on the floor.
# That is the same failure _PRICE_PANE / _OWN_PANE exist to prevent, one
# magnitude smaller, so it gets the same treatment: declared, not derived.
_SEPARATE_SCALE = frozenset({("bollinger_bands", "percent_b")})


def scale_for(name: str, key: str = "") -> str:
    """Which outputs may be measured against each other.

    Two series in one pane sharing this string share an axis. The default is
    the indicator's own name, because outputs of one indicator almost always
    belong together -- the exception is declared above.
    """
    if (name, key) in _SEPARATE_SCALE:
        return f"{name}:{key}"
    return name


def chartable() -> list[dict]:
    """The indicators a chart can offer, with their outputs and axes.

    Deliberately not the same answer as GET /api/indicators, which describes
    all forty for somebody writing a strategy -- signatures, descriptions, the
    lot. This one is the chart's question: which can be DRAWN, on which axis.
    They cannot drift about the set itself because both are built from
    `catalogue()`; what differs is pivot_points (no series to draw) and the
    pane, which only means anything on a chart.
    """
    out = []
    for spec in catalogue():
        if spec.name in UNCHARTABLE:
            continue
        keys = list(spec.keys) or [""]
        out.append(
            {
                "name": spec.name,
                "title": spec.title,
                "category": spec.category,
                # The Chinese label, so the picker can group by something a
                # reader recognises rather than by the enum value 「trend」.
                # CLAUDE.md: the audience is not an engineer.
                "category_label": INDICATOR_CATEGORIES.get(spec.category, spec.category),
                "outputs": [
                    {
                        "key": key,
                        "pane": pane_for(spec.name, key),
                        "scale": scale_for(spec.name, key),
                    }
                    for key in keys
                ],
                "params": [
                    {"name": p.name, "type": p.type, "default": p.default}
                    for p in spec.params
                    # The bar columns are bound from the candles; only the
                    # tuning knobs are anybody's choice.
                    if not p.type.startswith("list")
                ],
            }
        )
    return out
