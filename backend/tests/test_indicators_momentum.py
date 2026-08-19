"""Momentum indicators.

RSI carries the heaviest test here on purpose. It is the indicator the owner
names in their own strategy ("周線 RSI>80"), the one an AI is most likely to
hand-roll, and the one where hand-rolling goes wrong quietly: a plain EMA
instead of Wilder's smoothing shifts RSI by whole points, so an RSI>80 trigger
fires on the wrong candle -- or never.
"""

import pytest

from app.services.indicators import momentum
from tests.indicator_data import (
    BAR_COUNT,
    CLOSES,
    HIGHS,
    LOWS,
    RSI_CLOSES,
    VOLUMES,
    assert_series,
)

# Wilder's own worked example as published by StockCharts, to 4dp -- the
# reference every RSI implementation is measured against.
PUBLISHED_RSI_14 = [
    70.5328,
    66.3186,
    66.5522,
    69.4063,
    66.3556,
    57.9748,
    62.9296,
    63.2571,
    56.0596,
    62.3773,
    54.7076,
    50.4227,
    39.9898,
    41.4604,
    41.8689,
    45.4632,
    37.3040,
    33.0795,
    37.7729,
]


def test_rsi_reproduces_wilders_published_worked_example():
    result = momentum.rsi(RSI_CLOSES, 14)

    assert result[:14] == [None] * 14
    # The published table is itself rounded to 4dp from rounded closes, so it
    # pins the answer to about a hundredth of an RSI point -- far tighter than
    # the ~3 point error a plain-EMA "RSI" produces on this same series.
    assert result[14:] == pytest.approx(PUBLISHED_RSI_14, abs=0.005)


def test_rsi_first_value_is_the_simple_average_of_the_first_period_changes():
    """Wilder seeds with a simple average of the first 14 gains and losses and
    only then switches to his smoothing. Seeding with the smoothing itself,
    from the first change, is the error the `ta` package makes."""
    changes = [b - a for a, b in zip(RSI_CLOSES, RSI_CLOSES[1:], strict=False)]
    avg_gain = sum(max(c, 0.0) for c in changes[:14]) / 14
    avg_loss = sum(max(-c, 0.0) for c in changes[:14]) / 14

    expected = 100 - 100 / (1 + avg_gain / avg_loss)
    assert momentum.rsi(RSI_CLOSES, 14)[14] == pytest.approx(expected)


def test_rsi_on_the_shared_series():
    assert_series(
        momentum.rsi(CLOSES, 14),
        first_index=14,
        first=68.316832,
        tail_values=[43.633559, 47.418650, 50.594522, 53.254996],
    )


def test_rsi_pins_at_100_when_nothing_ever_falls():
    """Division by a zero average loss has to be handled, not crashed on --
    a series that only rises is exactly what a strategy sees in a breakout."""
    only_up = [float(100 + i) for i in range(20)]

    assert momentum.rsi(only_up, 14)[-1] == 100.0
    assert momentum.rsi(list(reversed(only_up)), 14)[-1] == 0.0


# --- stochastics -------------------------------------------------------------


def test_stochastic_k_locates_the_close_inside_the_period_range():
    result = momentum.stochastic(HIGHS, LOWS, CLOSES, period=14)

    highest, lowest = max(HIGHS[0:14]), min(LOWS[0:14])
    assert result["k"][13] == pytest.approx(100 * (CLOSES[13] - lowest) / (highest - lowest))
    assert_series(
        result["k"],
        first_index=13,
        first=62.857143,
        tail_values=[14.942529, 25.287356, 34.482759, 42.528736],
    )
    assert_series(
        result["d"],
        first_index=15,
        first=69.298942,
        tail_values=[7.527177, 14.586432, 24.904215, 34.099617],
    )


def test_slow_stochastic_k_is_the_fast_d():
    """The (14,3,3) "slow" stochastic is the fast one smoothed once more, so
    its %K must land exactly on the fast %D. Getting the two smoothing stages
    the wrong way round is invisible in a chart and wrong in a signal."""
    fast = momentum.stochastic(HIGHS, LOWS, CLOSES, period=14, k_smooth=1, d_period=3)
    slow = momentum.stochastic(HIGHS, LOWS, CLOSES, period=14, k_smooth=3, d_period=3)

    assert slow["k"] == fast["d"]
    assert_series(
        slow["d"],
        first_index=17,
        first=78.815290,
        tail_values=[11.865988, 10.313379, 15.672608, 24.530088],
    )


def test_stoch_rsi_is_a_stochastic_of_rsi_not_of_price():
    result = momentum.stoch_rsi(CLOSES)

    rsi = [x for x in momentum.rsi(CLOSES, 14) if x is not None]
    window = rsi[-14:]
    assert result["stoch_rsi"][-1] == pytest.approx(
        100 * (window[-1] - min(window)) / (max(window) - min(window))
    )
    assert_series(
        result["stoch_rsi"],
        first_index=27,  # 14 for the RSI, then 14 more to range it
        first=0.0,
        tail_values=[9.113727, 18.801895, 26.930730, 38.876316],
    )
    assert_series(
        result["k"],
        first_index=29,
        first=0.0,
        tail_values=[3.037909, 9.305207, 18.282117, 28.202980],
    )
    assert_series(
        result["d"],
        first_index=31,
        first=1.283945,
        tail_values=[5.355436, 5.046731, 10.208411, 18.596768],
    )


# --- the rest ----------------------------------------------------------------


def test_cci_divides_by_mean_absolute_deviation_not_standard_deviation():
    """Lambert's constant 0.015 is calibrated against mean absolute deviation.
    Substituting a standard deviation -- an easy slip, since every other band
    indicator uses one -- rescales CCI by roughly 20% and moves the ±100
    thresholds off the levels every chart draws them at."""
    result = momentum.cci(HIGHS, LOWS, CLOSES, 20)

    typical = [(h + lo + c) / 3 for h, lo, c in zip(HIGHS, LOWS, CLOSES, strict=True)]
    window = typical[:20]
    mean = sum(window) / 20
    mad = sum(abs(x - mean) for x in window) / 20
    assert result[19] == pytest.approx((typical[19] - mean) / (0.015 * mad))

    assert_series(
        result,
        first_index=19,
        first=128.696313,
        tail_values=[-138.933764, -99.685676, -63.869828, -36.861149],
    )


def test_williams_r_is_the_stochastic_measured_from_the_top():
    result = momentum.williams_r(HIGHS, LOWS, CLOSES, 14)

    highest, lowest = max(HIGHS[0:14]), min(LOWS[0:14])
    assert result[13] == pytest.approx(-100 * (highest - CLOSES[13]) / (highest - lowest))
    assert_series(
        result,
        first_index=13,
        first=-37.142857,
        tail_values=[-85.057471, -74.712644, -65.517241, -57.471264],
    )
    assert all(-100.0 <= x <= 0.0 for x in result[13:])


def test_roc_is_a_percentage_and_momentum_is_a_difference():
    """These two get confused constantly. ROC is scaled by the old price;
    momentum is not, so on a NT$1000 stock they differ by three orders of
    magnitude and a threshold tuned for one is meaningless for the other."""
    roc = momentum.roc(CLOSES, 12)
    mom = momentum.momentum(CLOSES, 10)

    assert roc[12] == pytest.approx(100 * (CLOSES[12] / CLOSES[0] - 1))
    assert mom[10] == pytest.approx(CLOSES[10] - CLOSES[0])
    assert_series(
        roc,
        first_index=12,
        first=7.465619,
        tail_values=[-10.447761, -10.147300, -7.781457, -4.729730],
    )
    assert_series(mom, first_index=10, first=5.2, tail_values=[-6.4, -4.3, -2.2, 0.1])


def test_mfi_is_rsi_weighted_by_money_flow():
    result = momentum.mfi(HIGHS, LOWS, CLOSES, VOLUMES, 14)

    assert_series(
        result,
        first_index=14,
        first=72.493217,
        tail_values=[39.755855, 39.031281, 37.023510, 34.154477],
    )
    assert all(0.0 <= x <= 100.0 for x in result[14:])


def test_mfi_and_rsi_diverge_when_volume_disagrees_with_price():
    """If MFI ignored volume it would just be RSI on the typical price. Two
    runs over the same prices with different volumes must not agree."""
    heavy_on_down_bars = [3000.0 if CLOSES[i] < CLOSES[i - 1] else 500.0 for i in range(BAR_COUNT)]
    heavy_on_down_bars[0] = 500.0

    baseline = momentum.mfi(HIGHS, LOWS, CLOSES, VOLUMES, 14)
    skewed = momentum.mfi(HIGHS, LOWS, CLOSES, heavy_on_down_bars, 14)
    assert skewed[-1] < baseline[-1] - 10


def test_tsi_double_smooths_momentum():
    result = momentum.tsi(CLOSES, long_period=8, short_period=4, signal_period=4)

    assert_series(
        result["tsi"],
        first_index=11,
        first=61.986646,
        tail_values=[-41.686519, -28.703814, -13.433617, 1.431673],
    )
    assert_series(
        result["signal"],
        first_index=14,
        first=35.136498,
        tail_values=[-38.574253, -34.626077, -26.149093, -15.116787],
    )


def test_tsi_at_its_default_lengths_needs_more_history_than_this_series_has():
    """25/13 double smoothing does not settle until bar 37, and its 13-bar
    signal line never does inside 40 bars. All-None is the correct answer;
    quietly returning early values would be the dangerous one."""
    result = momentum.tsi(CLOSES)

    assert result["tsi"][:37] == [None] * 37
    assert result["tsi"][37] == pytest.approx(0.908890)
    assert result["signal"] == [None] * BAR_COUNT


def test_ultimate_oscillator_weights_three_lookbacks_4_2_1():
    result = momentum.ultimate_oscillator(HIGHS, LOWS, CLOSES)

    assert_series(
        result,
        first_index=28,
        first=52.219645,
        tail_values=[44.951405, 50.730231, 50.503763, 50.276932],
    )
    assert all(0.0 <= x <= 100.0 for x in result[28:])


def test_cmo_is_algebraically_twice_a_simple_rsi_minus_100():
    """Chande's oscillator uses plain sums, not Wilder smoothing, which makes
    it exactly 2*RSI-100 for an RSI built the same way. That identity is an
    independent check on both the sums and the scaling."""
    result = momentum.cmo(CLOSES, 14)

    for i in range(14, BAR_COUNT):
        window = CLOSES[i - 14 : i + 1]
        ups = sum(max(b - a, 0.0) for a, b in zip(window, window[1:], strict=False))
        downs = sum(max(a - b, 0.0) for a, b in zip(window, window[1:], strict=False))
        simple_rsi = 100 - 100 / (1 + ups / downs)
        assert result[i] == pytest.approx(2 * simple_rsi - 100)

    assert_series(
        result,
        first_index=14,
        first=36.633663,
        tail_values=[-30.201342, -30.201342, -31.081081, -31.972789],
    )


def test_momentum_indicators_reject_mismatched_series():
    with pytest.raises(ValueError, match="volumes"):
        momentum.mfi(HIGHS, LOWS, CLOSES, VOLUMES[:5], 14)
    with pytest.raises(ValueError, match="highs"):
        momentum.stochastic(HIGHS[:5], LOWS, CLOSES)
