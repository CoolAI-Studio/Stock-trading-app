"""Trend indicators, asserted against independently-derived values.

WHERE THE EXPECTED NUMBERS COME FROM. Every value below was produced by a
separate naive from-the-definition implementation and then cross-checked
against pandas and the `ta` PyPI package on this exact series. Where our
answer and `ta`'s disagree, the disagreement is deliberate and is pinned by a
test of its own further down -- `ta` is not Wilder-correct, which is the
whole reason this library is hand-rolled rather than pulled from PyPI:

  * EMA seeding. `ta` seeds its EMA from the first sample; we seed from the
    SMA of the first `period` samples, which is what TA-Lib, StockCharts and
    Pine Script's ta.ema() all do. Everything built on an EMA (MACD, TRIX,
    Keltner, TSI, force index) inherits the difference.
  * ATR/Wilder smoothing. `ta` places its first ATR one bar early because it
    counts a true range for bar 0, which has no previous close to measure
    against.
  * PSAR. `ta` leaves un-computed bars filled with the close price.

`ta`'s SMA, WMA, Bollinger, Donchian, CCI, Williams %R, ROC, stochastic,
Aroon, ADX, A/D, CMF, MFI and Ichimoku all agree with us exactly, so those
are genuinely two independent implementations reaching the same answer.
"""

import pytest

from app.services.indicators import trend
from tests.indicator_data import (
    BAR_COUNT,
    CLOSES,
    HIGHS,
    LOWS,
    VOLUMES,
    assert_series,
)

# --- simple averages: hand-computable in the assertion itself ---------------


def test_sma_is_the_arithmetic_mean_of_the_window():
    result = trend.sma(CLOSES, 5)

    # (50.90 + 51.50 + 52.40 + 51.80 + 52.70) / 5
    assert result[4] == pytest.approx(259.30 / 5)
    assert_series(result, first_index=4, first=51.86, tail_values=[54.86, 54.46, 54.44, 54.84])


def test_wma_weights_the_window_1_to_n():
    result = trend.wma(CLOSES, 5)

    # (50.90*1 + 51.50*2 + 52.40*3 + 51.80*4 + 52.70*5) / (1+2+3+4+5)
    assert result[4] == pytest.approx(781.80 / 15)
    assert_series(
        result, first_index=4, first=52.12, tail_values=[54.30, 54.313333, 54.726667, 55.38]
    )


def test_ema_is_seeded_with_the_sma_of_the_first_period_values():
    """The seed is the single most common way to get an EMA subtly wrong, so
    it is asserted directly rather than only through the tail."""
    result = trend.ema(CLOSES, 10)

    assert result[9] == pytest.approx(sum(CLOSES[:10]) / 10)  # the seed itself
    # then alpha = 2/(10+1): 53.07 + (56.10 - 53.07) * 2/11
    assert result[10] == pytest.approx(53.07 + (56.10 - 53.07) * 2 / 11)
    assert_series(
        result,
        first_index=9,
        first=53.07,
        tail_values=[55.596920, 55.470207, 55.511988, 55.673445],
    )


def test_dema_removes_one_layer_of_ema_lag():
    result = trend.dema(CLOSES, 5)

    # DEMA = 2*EMA - EMA(EMA), so it cannot start until the inner EMA has
    # itself produced `period` values: index 2*5-2 = 8.
    assert_series(
        result,
        first_index=8,
        first=54.485778,
        tail_values=[53.626450, 54.114426, 54.863035, 55.677635],
    )


def test_tema_removes_two_layers_and_starts_correspondingly_later():
    result = trend.tema(CLOSES, 5)

    assert_series(
        result,
        first_index=12,  # 3*5 - 3
        first=55.098697,
        tail_values=[53.545517, 54.322329, 55.280625, 56.196817],
    )


def test_hma_is_a_wma_of_the_doubled_half_length_wma():
    result = trend.hma(CLOSES, 9)

    assert_series(
        result,
        first_index=10,
        first=55.705185,
        tail_values=[53.559259, 53.472593, 54.164074, 55.294074],
    )


def test_vwma_weights_each_close_by_its_own_volume():
    result = trend.vwma(CLOSES, VOLUMES, 5)

    numerator = sum(c * v for c, v in zip(CLOSES[:5], VOLUMES[:5], strict=True))
    assert result[4] == pytest.approx(numerator / sum(VOLUMES[:5]))
    assert_series(
        result,
        first_index=4,
        first=51.846875,
        tail_values=[54.752941, 54.397253, 54.274556, 54.564238],
    )


def test_kama_speeds_up_with_the_efficiency_ratio():
    result = trend.kama(CLOSES, 10)

    assert result[9] == pytest.approx(sum(CLOSES[:10]) / 10)  # same seeding rule as EMA
    assert_series(
        result,
        first_index=9,
        first=53.07,
        tail_values=[56.142251, 56.035129, 56.023388, 56.025265],
    )


def test_kama_tracks_a_straight_line_almost_exactly():
    """A perfectly efficient market (every step in the same direction) drives
    the efficiency ratio to 1, so KAMA runs at the fast constant and sits on
    top of price. This is the property that separates KAMA from a plain EMA."""
    ramp = [float(100 + i) for i in range(40)]

    fast_alpha = 2 / (2 + 1)
    result = trend.kama(ramp, 10)

    # ER = 1 pins the smoothing constant at fast_alpha**2 = 4/9, which leaves
    # KAMA a shade over one step behind a line rising one unit per bar.
    assert fast_alpha**2 == pytest.approx(4 / 9)
    assert result[-1] == pytest.approx(ramp[-1] - 1.25, abs=0.01)
    # ...and an EMA of the same length lags more than twice as far.
    assert trend.ema(ramp, 10)[-1] < result[-1] - 3


# --- MACD: the indicator the owner's own strategy turns on ------------------


def test_macd_line_is_the_gap_between_the_two_emas():
    result = trend.macd(CLOSES)

    fast, slow = trend.ema(CLOSES, 12), trend.ema(CLOSES, 26)
    assert result["macd"][-1] == pytest.approx(fast[-1] - slow[-1])
    assert_series(
        result["macd"],
        first_index=25,
        first=2.583468,
        tail_values=[0.098619, 0.017720, 0.017954, 0.073773],
    )


def test_macd_signal_is_an_ema_of_the_macd_line_not_of_price():
    """Signalling off an EMA of price instead of an EMA of the MACD line is a
    classic hand-rolled mistake: it produces a plausible line that crosses at
    the wrong times."""
    result = trend.macd(CLOSES)

    assert_series(
        result["signal"],
        first_index=33,  # 26-1 for the line, then 9-1 more for its own EMA
        first=1.819995,
        tail_values=[1.077168, 0.865279, 0.695814, 0.571405],
    )


def test_macd_histogram_is_line_minus_signal_everywhere():
    result = trend.macd(CLOSES)

    assert_series(
        result["histogram"],
        first_index=33,
        first=pytest.approx(result["macd"][33] - result["signal"][33]),
        tail_values=[-0.978549, -0.847558, -0.677860, -0.497633],
    )


def test_macd_reports_the_line_before_the_signal_exists():
    """The line is useful eight bars before the signal line can be computed,
    so it is not withheld until then -- but the signal must stay None, never
    zero, or a strategy watching for a crossover fires on the warm-up."""
    result = trend.macd(CLOSES)

    assert result["macd"][30] is not None
    assert result["signal"][30] is None


# --- Wilder's directional system --------------------------------------------


def test_adx_and_the_two_directional_indicators():
    result = trend.adx(HIGHS, LOWS, CLOSES, 14)

    assert_series(
        result["plus_di"],
        first_index=14,
        first=22.594142,
        tail_values=[14.001922, 15.798082, 18.266292, 19.796321],
    )
    assert_series(
        result["minus_di"],
        first_index=14,
        first=5.857741,
        tail_values=[22.106391, 20.645222, 19.273318, 17.986173],
    )
    # ADX is Wilder-smoothed DX, so it needs a second full period on top:
    # 2*14 - 1 = 27.
    assert_series(
        result["adx"],
        first_index=27,
        first=73.046103,
        tail_values=[43.740224, 41.565958, 38.788573, 36.360173],
    )


def test_adx_di_lines_swap_when_the_trend_does():
    result = trend.adx(HIGHS, LOWS, CLOSES, 14)

    # Bar 25 is the top of the second up leg; bar 35 is the bottom of the drop.
    assert result["plus_di"][25] > 10 * result["minus_di"][25]
    assert result["minus_di"][35] > result["plus_di"][35]


def test_aroon_reads_100_on_the_bar_that_sets_the_extreme():
    result = trend.aroon(HIGHS, LOWS, 25)

    # Bar 25 is the highest high of its own 26-bar window, so Aroon Up is 100.
    assert HIGHS[25] == max(HIGHS[0:26])
    assert_series(result["up"], first_index=25, first=100.0, tail_values=[60.0, 56.0, 52.0, 48.0])
    # ...and the lowest low of that same window is bar 0, 25 bars back, so
    # Aroon Down has decayed all the way to zero.
    assert LOWS[0] == min(LOWS[0:26])
    assert_series(result["down"], first_index=25, first=0.0, tail_values=[100.0, 96.0, 92.0, 88.0])
    assert result["oscillator"][-1] == pytest.approx(result["up"][-1] - result["down"][-1])


# --- Ichimoku: the one where alignment is the whole indicator ---------------


def test_ichimoku_returns_the_cloud_already_shifted_forward():
    """Senkou A/B are drawn `displacement` bars AHEAD of the bars they were
    computed from. Returning them unshifted would let a strategy compare
    today's price against a cloud that in reality is not drawn until 26 bars
    from now -- it would look right and be reading the future."""
    result = trend.ichimoku(
        HIGHS, LOWS, CLOSES, conversion_period=3, base_period=5, span_b_period=8, displacement=5
    )

    conversion, base = result["conversion"], result["base"]
    assert conversion[-1] == pytest.approx((max(HIGHS[-3:]) + min(LOWS[-3:])) / 2)
    assert base[-1] == pytest.approx((max(HIGHS[-5:]) + min(LOWS[-5:])) / 2)

    # span_a at the last bar is the average of conversion and base as they
    # stood `displacement` bars ago.
    assert result["span_a"][-1] == pytest.approx((conversion[-6] + base[-6]) / 2)
    assert result["span_b"][-1] == pytest.approx((max(HIGHS[-13:-5]) + min(LOWS[-13:-5])) / 2)

    assert result["span_a"][-3:] == pytest.approx([56.525, 56.15, 55.65])
    assert result["span_b"][-3:] == pytest.approx([57.95, 57.95, 57.6])


def test_ichimoku_lagging_span_runs_out_before_the_last_bar():
    """Chikou is the close plotted `displacement` bars BACK, so the most
    recent bars have no value yet -- the close that belongs there has not
    happened. None is the only honest answer; a copy of today's close would
    silently turn the indicator into a lookahead."""
    result = trend.ichimoku(HIGHS, LOWS, CLOSES, displacement=26)

    assert result["lagging"][0] == pytest.approx(CLOSES[26])
    assert result["lagging"][BAR_COUNT - 27] == pytest.approx(CLOSES[-1])
    assert result["lagging"][BAR_COUNT - 26 :] == [None] * 26


# --- Parabolic SAR -----------------------------------------------------------


def test_parabolic_sar_accelerates_then_flips_on_penetration():
    result = trend.parabolic_sar(HIGHS, LOWS)

    assert_series(
        result,
        first_index=1,
        first=49.60,
        tail_values=[58.107924, 57.350814, 56.699700, 52.70],
    )
    # Bar 3: SAR + AF*(EP - SAR). Bar 2 set a new high, so the acceleration
    # factor has already stepped from 0.02 to 0.04 and EP is that new high --
    # forgetting to bump AF is the classic way to get a too-slow SAR.
    assert result[3] == pytest.approx(49.60 + 0.04 * (52.80 - 49.60))
    # Bar 39 breaks above the falling SAR, so it flips to the prior trend's
    # extreme point -- the lowest low since the downtrend began -- rather than
    # continuing to drift.
    assert HIGHS[39] > result[38]
    assert result[39] == pytest.approx(min(LOWS[28:39]))


def test_parabolic_sar_flips_sides_only_at_a_reversal():
    result = trend.parabolic_sar(HIGHS, LOWS)

    side = [None if x is None else (1 if x < CLOSES[i] else -1) for i, x in enumerate(result)]
    flips = [i for i in range(2, BAR_COUNT) if side[i] != side[i - 1]]
    assert flips == [13, 16, 28, 39]
    # A stop-and-reverse that never reverses, or reverses on every bar, both
    # pass a "returns 40 numbers" check and are both useless.
    assert side[1] == 1


# --- SuperTrend --------------------------------------------------------------


def test_supertrend_follows_the_band_on_the_far_side_of_price():
    result = trend.supertrend(HIGHS, LOWS, CLOSES, 10, 3.0)

    assert_series(
        result["supertrend"],
        first_index=10,
        first=60.72,
        tail_values=[59.181129] * 4,
    )
    for i in range(10, BAR_COUNT):
        if result["direction"][i] == 1:
            assert result["supertrend"][i] <= CLOSES[i]
        else:
            assert result["supertrend"][i] >= CLOSES[i]


def test_supertrend_direction_flips_exactly_twice_on_this_series():
    result = trend.supertrend(HIGHS, LOWS, CLOSES, 10, 3.0)

    direction = result["direction"][10:]
    flips = [i for i in range(1, len(direction)) if direction[i] != direction[i - 1]]
    # Into the second up leg at bar 23, back out at bar 30.
    assert [i + 10 for i in flips] == [23, 30]
    assert direction[0] == -1


# --- TRIX --------------------------------------------------------------------


def test_trix_is_the_percent_change_of_a_triple_smoothed_ema():
    result = trend.trix(CLOSES, period=5, signal_period=9)

    assert_series(
        result["trix"],
        first_index=13,  # 3*5 - 2, then one more bar to have a change to measure
        first=0.476577,
        tail_values=[-0.796174, -0.710587, -0.529607, -0.299863],
    )
    assert_series(
        result["signal"],
        first_index=21,
        first=0.459030,
        tail_values=[-0.396871, -0.459614, -0.473613, -0.438863],
    )


# --- shared contract ---------------------------------------------------------


def test_a_series_shorter_than_the_period_is_all_none_not_an_error():
    """Warming up is the normal state of a freshly saved strategy, not a
    fault. Raising here would trip the consecutive-error guard and retire the
    strategy before it ever saw enough candles to run."""
    assert trend.sma([1.0, 2.0], 20) == [None, None]
    assert trend.ema([1.0, 2.0], 20) == [None, None]
    assert trend.macd([1.0, 2.0])["signal"] == [None, None]
    assert trend.adx([2.0], [1.0], [1.5], 14)["adx"] == [None]
    assert trend.parabolic_sar([], []) == []


def test_mismatched_input_lengths_are_rejected_by_name():
    with pytest.raises(ValueError, match="volumes"):
        trend.vwma(CLOSES, VOLUMES[:-1], 5)
    with pytest.raises(ValueError, match="lows"):
        trend.adx(HIGHS, LOWS[:-1], CLOSES)


@pytest.mark.parametrize("bad", [0, -3, 2.5, True, "14"])
def test_a_nonsense_period_is_rejected(bad):
    with pytest.raises(ValueError, match="period"):
        trend.sma(CLOSES, bad)


def test_a_non_numeric_price_is_rejected_rather_than_silently_dropped():
    """bar.volume is `float | None` upstream, so a strategy that appends it
    blindly will hand us a None. Failing loudly beats returning an average
    computed over a hole."""
    with pytest.raises(ValueError, match="values"):
        trend.sma([1.0, None, 3.0], 2)
