"""Volatility indicators.

ATR is the load-bearing one: SuperTrend and Keltner are both built on it, and
it is the second place (after RSI) where Wilder's smoothing is routinely
replaced with a plain EMA.
"""

import pytest

from app.services.indicators import trend, volatility
from tests.indicator_data import BAR_COUNT, CLOSES, HIGHS, LOWS, assert_series


def test_stdev_is_the_population_deviation_of_the_window():
    """Population (n), not sample (n-1). Bollinger Bands are defined on the
    population figure, and using n-1 widens a 20-period band by ~2.6% -- small
    enough to look right, large enough to change which bars touch it."""
    result = volatility.stdev(CLOSES, 20)

    window = CLOSES[:20]
    mean = sum(window) / 20
    expected = (sum((x - mean) ** 2 for x in window) / 20) ** 0.5
    assert result[19] == pytest.approx(expected)
    # ...and it is measurably not the sample deviation.
    sample = (sum((x - mean) ** 2 for x in window) / 19) ** 0.5
    assert result[19] != pytest.approx(sample, rel=1e-3)

    assert_series(
        result,
        first_index=19,
        first=1.805955,
        tail_values=[2.165750, 2.230308, 2.250217, 2.256208],
    )


def test_bollinger_bands_sit_num_std_either_side_of_the_sma():
    result = volatility.bollinger_bands(CLOSES, 20, 2.0)

    middle = result["middle"]
    deviation = volatility.stdev(CLOSES, 20)
    assert middle[-1] == pytest.approx(sum(CLOSES[-20:]) / 20)
    assert result["upper"][-1] == pytest.approx(middle[-1] + 2.0 * deviation[-1])
    assert result["lower"][-1] == pytest.approx(middle[-1] - 2.0 * deviation[-1])

    assert_series(
        result["upper"],
        first_index=19,
        first=58.006911,
        tail_values=[61.676501, 61.695617, 61.695433, 61.657416],
    )
    assert_series(
        result["lower"],
        first_index=19,
        first=50.783089,
        tail_values=[53.013499, 52.774383, 52.694567, 52.632584],
    )
    assert_series(
        result["middle"], first_index=19, first=54.395, tail_values=[57.345, 57.235, 57.195, 57.145]
    )


def test_bollinger_bandwidth_and_percent_b():
    """%B is 1 at the upper band and 0 at the lower one; bandwidth is the
    band width as a percentage of the middle. Both are ratios, so getting the
    scaling wrong is invisible until a threshold is set against them."""
    result = volatility.bollinger_bands(CLOSES, 20, 2.0)

    span = result["upper"][-1] - result["lower"][-1]
    assert result["percent_b"][-1] == pytest.approx((CLOSES[-1] - result["lower"][-1]) / span)
    assert result["bandwidth"][-1] == pytest.approx(100 * span / result["middle"][-1])

    assert_series(
        result["bandwidth"],
        first_index=19,
        first=13.280305,
        tail_values=[15.106813, 15.587024, 15.737156, 15.792864],
    )
    assert_series(
        result["percent_b"],
        first_index=19,
        first=0.915985,
        tail_values=[0.113875, 0.238265, 0.333905, 0.417450],
    )


def test_bollinger_bands_collapse_onto_price_when_it_does_not_move():
    flat = [42.0] * 25

    result = volatility.bollinger_bands(flat, 20, 2.0)
    assert result["upper"][-1] == pytest.approx(42.0)
    assert result["lower"][-1] == pytest.approx(42.0)
    # A zero-width band makes %B a division by zero -- None, not a crash and
    # not a silent 0.0 that would read as "at the lower band".
    assert result["percent_b"][-1] is None
    assert result["bandwidth"][-1] == pytest.approx(0.0)


# --- ATR ---------------------------------------------------------------------


def test_atr_seeds_with_the_simple_mean_of_the_first_period_true_ranges():
    result = volatility.atr(HIGHS, LOWS, CLOSES, 14)

    true_ranges = [
        max(HIGHS[i] - LOWS[i], abs(HIGHS[i] - CLOSES[i - 1]), abs(LOWS[i] - CLOSES[i - 1]))
        for i in range(1, BAR_COUNT)
    ]
    # The first true range belongs to bar 1, not bar 0 -- bar 0 has no previous
    # close to gap from. Counting a range for bar 0 puts the whole series one
    # bar early, which is what the `ta` package does.
    assert result[13] is None
    assert result[14] == pytest.approx(sum(true_ranges[:14]) / 14)
    # Then Wilder's smoothing, which is (prev*13 + new)/14, NOT a 2/(n+1) EMA.
    assert result[15] == pytest.approx((result[14] * 13 + true_ranges[14]) / 14)

    assert_series(
        result,
        first_index=14,
        first=1.707143,
        tail_values=[1.847671, 1.837123, 1.827329, 1.818234],
    )


def test_atr_counts_the_gap_not_just_the_bar_range():
    """A bar that opens far above the previous close has a true range larger
    than its own high-low. An "ATR" that only averages high-low understates
    exactly the volatility a stop is meant to survive."""
    highs = [10.0, 10.5, 20.2]
    lows = [9.0, 9.5, 20.0]
    closes = [9.5, 10.0, 20.1]

    result = volatility.atr(highs, lows, closes, 2)
    # bar 1: max(1.0, |10.5-9.5|, |9.5-9.5|) = 1.0
    # bar 2: max(0.2, |20.2-10.0|, |20.0-10.0|) = 10.2  <- the gap, not 0.2
    assert result[2] == pytest.approx((1.0 + 10.2) / 2)


# --- channels ----------------------------------------------------------------


def test_keltner_channels_are_an_ema_middle_with_atr_width():
    """Keltner uses an ATR width around an EMA; Bollinger uses a standard
    deviation around an SMA. Mixing the two halves is the standard mistake and
    produces a channel that looks plausible on any chart."""
    result = volatility.keltner_channels(HIGHS, LOWS, CLOSES, 20, 10, 2.0)

    assert result["middle"][-1] == pytest.approx(trend.ema(CLOSES, 20)[-1])
    atr = volatility.atr(HIGHS, LOWS, CLOSES, 10)
    assert result["upper"][-1] == pytest.approx(result["middle"][-1] + 2.0 * atr[-1])

    assert_series(
        result["upper"],
        first_index=19,
        first=57.774061,
        tail_values=[59.815912, 59.669853, 59.614064, 59.630406],
    )
    assert_series(
        result["middle"],
        first_index=19,
        first=54.395,
        tail_values=[56.061826, 55.951176, 55.927254, 55.972278],
    )
    assert_series(
        result["lower"],
        first_index=19,
        first=51.015939,
        tail_values=[52.307740, 52.232498, 52.240445, 52.314149],
    )


def test_donchian_channels_are_the_period_high_and_low():
    result = volatility.donchian_channels(HIGHS, LOWS, 20)

    assert result["upper"][-1] == pytest.approx(max(HIGHS[-20:]))
    assert result["lower"][-1] == pytest.approx(min(LOWS[-20:]))
    assert result["middle"][-1] == pytest.approx((result["upper"][-1] + result["lower"][-1]) / 2)

    assert_series(result["upper"], first_index=19, first=57.8, tail_values=[61.4] * 4)
    assert_series(result["lower"], first_index=19, first=49.6, tail_values=[52.7] * 4)
    assert_series(result["middle"], first_index=19, first=53.7, tail_values=[57.05] * 4)


def test_donchian_uses_the_highs_and_lows_not_the_closes():
    """A Donchian breakout built from closes never sees the intrabar high, so
    it misses the breakout it exists to catch."""
    result = volatility.donchian_channels(HIGHS, LOWS, 20)

    assert result["upper"][-1] > max(CLOSES[-20:])


def test_volatility_indicators_warm_up_quietly():
    assert volatility.atr([2.0], [1.0], [1.5], 14) == [None]
    assert volatility.bollinger_bands([1.0, 2.0], 20)["upper"] == [None, None]
    assert volatility.donchian_channels([2.0], [1.0], 20)["upper"] == [None]


def test_volatility_indicators_reject_mismatched_series():
    with pytest.raises(ValueError, match="closes"):
        volatility.atr(HIGHS, LOWS, CLOSES[:-1], 14)
    with pytest.raises(ValueError, match="lows"):
        volatility.donchian_channels(HIGHS, LOWS[:3], 20)
