"""Price transforms."""

import pytest

from app.services.indicators import price
from tests.indicator_data import BAR_COUNT, CLOSES, HIGHS, LOWS, OPENS, assert_series


def test_typical_price_is_the_mean_of_high_low_and_close():
    result = price.typical_price(HIGHS, LOWS, CLOSES)

    assert result[0] == pytest.approx((51.20 + 49.60 + 50.90) / 3)
    assert_series(
        result,
        first_index=0,
        first=50.566667,
        tail_values=[53.700000, 54.466667, 55.333333, 56.033333],
    )


# --- Heikin Ashi -------------------------------------------------------------


def test_heikin_ashi_open_is_recursive_and_close_is_the_ohlc_average():
    """The HA open depends on the PREVIOUS HA candle, not the previous real
    one. Substituting the real open produces candles that look almost right
    and smooth nothing -- which is the entire point of the transform."""
    result = price.heikin_ashi(OPENS, HIGHS, LOWS, CLOSES)

    # The very first candle has no predecessor, so it seeds from the real bar.
    assert result["open"][0] == pytest.approx((OPENS[0] + CLOSES[0]) / 2)
    assert result["close"][0] == pytest.approx((OPENS[0] + HIGHS[0] + LOWS[0] + CLOSES[0]) / 4)

    for i in range(1, BAR_COUNT):
        assert result["open"][i] == pytest.approx(
            (result["open"][i - 1] + result["close"][i - 1]) / 2
        )
        assert result["close"][i] == pytest.approx((OPENS[i] + HIGHS[i] + LOWS[i] + CLOSES[i]) / 4)

    assert_series(
        result["open"],
        first_index=0,
        first=50.45,
        tail_values=[54.749002, 54.149501, 54.237250, 54.718625],
    )
    assert_series(
        result["close"],
        first_index=0,
        first=50.425,
        tail_values=[53.550000, 54.325000, 55.200000, 55.925000],
    )


def test_heikin_ashi_high_and_low_include_the_synthetic_body():
    """HA high/low are the extremes of the real high/low AND the synthetic
    open/close, so the body can never poke outside the wick."""
    result = price.heikin_ashi(OPENS, HIGHS, LOWS, CLOSES)

    for i in range(BAR_COUNT):
        assert result["high"][i] == pytest.approx(
            max(HIGHS[i], result["open"][i], result["close"][i])
        )
        assert result["low"][i] == pytest.approx(
            min(LOWS[i], result["open"][i], result["close"][i])
        )

    assert_series(
        result["high"],
        first_index=0,
        first=51.20,
        tail_values=[54.749002, 55.100000, 56.000000, 56.700000],
    )
    assert_series(
        result["low"],
        first_index=0,
        first=49.60,
        tail_values=[52.700000, 53.400000, 54.237250, 54.718625],
    )


def test_heikin_ashi_smooths_away_single_bar_noise():
    """One outlier bar in an otherwise flat series must move the HA close by
    much less than it moves the real close."""
    flat_open = [10.0] * 6
    flat = [10.0] * 6
    spiked = [10.0, 10.0, 10.0, 14.0, 10.0, 10.0]

    result = price.heikin_ashi(flat_open, spiked, flat, spiked)
    assert result["close"][3] == pytest.approx((10.0 + 14.0 + 10.0 + 14.0) / 4)
    assert result["close"][3] < spiked[3]


# --- pivot points ------------------------------------------------------------


def test_classic_pivot_points_from_the_previous_period_hlc():
    """Pivots are computed from the PREVIOUS period's high/low/close and then
    used as levels during the current one -- which is why this takes three
    numbers rather than three series."""
    result = price.pivot_points(56.70, 55.00, 56.40)

    pivot = (56.70 + 55.00 + 56.40) / 3
    assert result["p"] == pytest.approx(pivot)
    assert result["r1"] == pytest.approx(2 * pivot - 55.00)
    assert result["s1"] == pytest.approx(2 * pivot - 56.70)
    assert result["r2"] == pytest.approx(pivot + (56.70 - 55.00))
    assert result["s2"] == pytest.approx(pivot - (56.70 - 55.00))
    assert result["r3"] == pytest.approx(56.70 + 2 * (pivot - 55.00))
    assert result["s3"] == pytest.approx(55.00 - 2 * (56.70 - pivot))

    assert result == pytest.approx(
        {
            "p": 56.033333,
            "r1": 57.066667,
            "r2": 57.733333,
            "r3": 58.766667,
            "s1": 55.366667,
            "s2": 54.333333,
            "s3": 53.666667,
        },
        abs=1e-6,
    )


def test_fibonacci_pivot_points_use_retracements_of_the_period_range():
    result = price.pivot_points(56.70, 55.00, 56.40, method="fibonacci")

    span = 56.70 - 55.00
    pivot = (56.70 + 55.00 + 56.40) / 3
    assert result["r1"] == pytest.approx(pivot + 0.382 * span)
    assert result["r2"] == pytest.approx(pivot + 0.618 * span)
    assert result["r3"] == pytest.approx(pivot + span)
    assert result["s1"] == pytest.approx(pivot - 0.382 * span)


def test_pivot_levels_come_out_in_order():
    result = price.pivot_points(56.70, 55.00, 56.40)

    levels = [result[k] for k in ("s3", "s2", "s1", "p", "r1", "r2", "r3")]
    assert levels == sorted(levels)


def test_an_unknown_pivot_method_says_which_ones_exist():
    with pytest.raises(ValueError, match="classic"):
        price.pivot_points(56.70, 55.00, 56.40, method="camarilla")


def test_price_transforms_reject_mismatched_series():
    with pytest.raises(ValueError, match="opens"):
        price.heikin_ashi(OPENS[:3], HIGHS, LOWS, CLOSES)
    with pytest.raises(ValueError, match="highs"):
        price.typical_price(HIGHS[:3], LOWS, CLOSES)
