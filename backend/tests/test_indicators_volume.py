"""Volume indicators."""

import pytest

from app.services.indicators import trend, volume
from tests.indicator_data import BAR_COUNT, CLOSES, HIGHS, LOWS, VOLUMES, assert_series


def test_obv_adds_volume_on_up_closes_and_subtracts_it_on_down_closes():
    result = volume.obv(CLOSES, VOLUMES)

    # 50.90 -> 51.50 up, +1350; -> 52.40 up, +1100; -> 51.80 down, -1500.
    assert result[:4] == pytest.approx([0.0, 1350.0, 2450.0, 950.0])
    assert_series(result, first_index=0, first=0.0, tail_values=[8700.0, 10050.0, 11300.0, 12450.0])


def test_obv_starts_from_zero_so_only_its_slope_carries_meaning():
    """The absolute level of OBV is arbitrary -- Pine's ta.obv starts at zero
    and so do we. A strategy must compare OBV to its own past, never to a
    number, and starting elsewhere would make that mistake harder to spot."""
    assert volume.obv(CLOSES, VOLUMES)[0] == 0.0


def test_obv_ignores_volume_on_an_unchanged_close():
    closes = [10.0, 10.0, 10.0]

    assert volume.obv(closes, [100.0, 200.0, 300.0]) == [0.0, 0.0, 0.0]


def test_vwap_is_volume_weighted_typical_price_from_the_start_of_the_series():
    result = volume.vwap(HIGHS, LOWS, CLOSES, VOLUMES)

    typical = [(h + lo + c) / 3 for h, lo, c in zip(HIGHS, LOWS, CLOSES, strict=True)]
    weighted = sum(t * v for t, v in zip(typical, VOLUMES, strict=True))
    assert result[-1] == pytest.approx(weighted / sum(VOLUMES))
    # The first bar's VWAP is just that bar's typical price.
    assert result[0] == pytest.approx(typical[0])

    assert_series(
        result,
        first_index=0,
        first=50.566667,
        tail_values=[55.940508, 55.907917, 55.896388, 55.898871],
    )


def test_vwap_with_a_period_becomes_a_rolling_window():
    result = volume.vwap(HIGHS, LOWS, CLOSES, VOLUMES, period=20)

    typical = [(h + lo + c) / 3 for h, lo, c in zip(HIGHS, LOWS, CLOSES, strict=True)]
    weighted = sum(t * v for t, v in zip(typical[-20:], VOLUMES[-20:], strict=True))
    assert result[-1] == pytest.approx(weighted / sum(VOLUMES[-20:]))
    assert_series(
        result,
        first_index=19,
        first=54.372232,
        tail_values=[57.301071, 57.223121, 57.172852, 57.139810],
    )


def test_accumulation_distribution_weights_volume_by_where_in_the_bar_it_closed():
    result = volume.accumulation_distribution(HIGHS, LOWS, CLOSES, VOLUMES)

    # bar 0: ((50.90-49.60) - (51.20-50.90)) / (51.20-49.60) * 1200
    assert result[0] == pytest.approx(((1.30 - 0.30) / 1.60) * 1200.0)
    assert_series(
        result,
        first_index=0,
        first=750.0,
        tail_values=[7279.223006, 8311.575947, 9120.399477, 9864.517124],
    )


def test_accumulation_distribution_treats_a_zero_range_bar_as_neutral():
    """A limit-up bar has high == low, so the multiplier divides by zero. It
    contributes nothing rather than crashing the strategy that asked for it --
    Taiwan equities hit their daily limit often enough for this to matter."""
    result = volume.accumulation_distribution([10.0, 10.0], [10.0, 10.0], [10.0, 10.0], [5.0, 5.0])

    assert result == [0.0, 0.0]


def test_chaikin_money_flow_is_the_ratio_of_flow_to_volume_over_the_window():
    result = volume.chaikin_money_flow(HIGHS, LOWS, CLOSES, VOLUMES, 20)

    flows = []
    for i in range(BAR_COUNT - 20, BAR_COUNT):
        span = HIGHS[i] - LOWS[i]
        flows.append(((CLOSES[i] - LOWS[i]) - (HIGHS[i] - CLOSES[i])) / span * VOLUMES[i])
    assert result[-1] == pytest.approx(sum(flows) / sum(VOLUMES[-20:]))

    assert_series(
        result,
        first_index=19,
        first=0.296237,
        tail_values=[0.009113, 0.003665, 0.042437, 0.041045],
    )
    assert all(-1.0 <= x <= 1.0 for x in result[19:])


def test_force_index_is_an_ema_of_price_change_times_volume():
    result = volume.force_index(CLOSES, VOLUMES, 13)

    assert_series(
        result,
        first_index=13,
        first=376.153846,
        tail_values=[-803.730864, -515.340741, -298.863492, -141.168708],
    )


def test_force_index_period_one_is_the_raw_unsmoothed_force():
    result = volume.force_index(CLOSES, VOLUMES, 1)

    assert result[0] is None  # no previous close to measure a change against
    assert result[1] == pytest.approx((CLOSES[1] - CLOSES[0]) * VOLUMES[1])
    assert_series(result, first_index=1, first=810.0, tail_values=[1200.0, 1215.0, 1000.0, 805.0])


def test_volume_oscillator_is_the_percentage_gap_between_two_volume_emas():
    result = volume.volume_oscillator(VOLUMES, 5, 10)

    short, long = trend.ema(VOLUMES, 5), trend.ema(VOLUMES, 10)
    assert result[-1] == pytest.approx(100 * (short[-1] - long[-1]) / long[-1])
    assert_series(
        result,
        first_index=9,
        first=6.619288,
        tail_values=[-0.384104, -4.696150, -8.038216, -10.746584],
    )


def test_volume_indicators_warm_up_and_validate():
    assert volume.chaikin_money_flow([2.0], [1.0], [1.5], [10.0], 20) == [None]
    assert volume.obv([], []) == []
    with pytest.raises(ValueError, match="volumes"):
        volume.obv(CLOSES, VOLUMES[:-1])
    with pytest.raises(ValueError, match="volumes"):
        volume.vwap(HIGHS, LOWS, CLOSES, [None] * BAR_COUNT)
