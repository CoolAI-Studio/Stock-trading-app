"""The fixed price series every indicator test asserts against.

Two datasets, both frozen: nothing here may be regenerated from the code
under test, because then the tests would only prove the code agrees with
itself.

RSI_CLOSES is Wilder's own worked example as published by StockCharts, kept
to its original 4 decimal places -- it is the one series in this repo whose
correct RSI is a matter of public record.

The 40-bar OHLCV set is hand-written (an up leg, a pullback, a second up leg,
a sharp drop, a bounce) so that trend, reversal and range indicators all have
something real to bite on. Every expected value asserted against it was
produced by a separate, deliberately naive from-the-definition implementation
and cross-checked against pandas and the `ta` package; see
tests/test_indicators_trend.py for where the two references disagree and why.
"""

RSI_CLOSES = [
    44.3389,
    44.0902,
    44.1497,
    43.6124,
    44.3278,
    44.8264,
    45.0955,
    45.4245,
    45.8433,
    46.0826,
    45.8931,
    46.0328,
    45.6140,
    46.2820,
    46.2820,
    46.0027,
    46.0328,
    46.4116,
    46.2222,
    45.6439,
    46.2122,
    46.2521,
    45.7137,
    46.4515,
    45.7835,
    45.3548,
    44.0288,
    44.1783,
    44.2181,
    44.5672,
    43.4205,
    42.6628,
    43.1314,
]

OPENS = [
    50.00,
    50.80,
    51.40,
    52.30,
    51.90,
    52.60,
    53.50,
    54.10,
    53.60,
    54.40,
    55.30,
    56.00,
    55.40,
    54.60,
    53.90,
    54.50,
    55.20,
    56.10,
    57.00,
    56.40,
    57.30,
    58.20,
    59.00,
    58.40,
    59.30,
    60.20,
    61.00,
    60.30,
    59.10,
    57.80,
    56.20,
    55.00,
    55.90,
    56.80,
    55.70,
    54.30,
    53.10,
    53.90,
    54.80,
    55.60,
]
HIGHS = [
    51.20,
    51.90,
    52.80,
    52.90,
    53.10,
    54.00,
    54.60,
    54.80,
    55.00,
    55.80,
    56.40,
    56.60,
    56.10,
    55.20,
    55.00,
    55.60,
    56.50,
    57.40,
    57.60,
    57.80,
    58.60,
    59.40,
    59.60,
    59.70,
    60.50,
    61.30,
    61.40,
    61.10,
    60.00,
    58.40,
    56.90,
    56.30,
    57.10,
    57.20,
    56.10,
    54.90,
    54.40,
    55.10,
    56.00,
    56.70,
]
LOWS = [
    49.60,
    50.30,
    51.00,
    51.50,
    51.40,
    52.10,
    53.00,
    53.20,
    53.10,
    53.90,
    54.90,
    54.80,
    54.20,
    53.50,
    53.40,
    54.00,
    54.80,
    55.70,
    56.10,
    56.00,
    56.80,
    57.70,
    58.10,
    58.00,
    58.90,
    59.70,
    59.80,
    58.90,
    57.50,
    56.00,
    54.60,
    54.50,
    55.40,
    55.30,
    54.10,
    52.90,
    52.70,
    53.40,
    54.30,
    55.00,
]
CLOSES = [
    50.90,
    51.50,
    52.40,
    51.80,
    52.70,
    53.60,
    54.20,
    53.70,
    54.50,
    55.40,
    56.10,
    55.30,
    54.70,
    54.00,
    54.60,
    55.30,
    56.20,
    57.10,
    56.50,
    57.40,
    58.30,
    59.10,
    58.50,
    59.40,
    60.30,
    61.10,
    60.40,
    59.20,
    57.90,
    56.30,
    55.10,
    56.00,
    56.90,
    55.80,
    54.40,
    53.20,
    54.00,
    54.90,
    55.70,
    56.40,
]
VOLUMES = [
    1200.0,
    1350.0,
    1100.0,
    1500.0,
    1250.0,
    1400.0,
    1600.0,
    1150.0,
    1300.0,
    1700.0,
    1800.0,
    1250.0,
    1050.0,
    1400.0,
    1500.0,
    1600.0,
    1750.0,
    1900.0,
    1200.0,
    1450.0,
    1650.0,
    1850.0,
    1300.0,
    1550.0,
    1950.0,
    2100.0,
    1400.0,
    1800.0,
    2200.0,
    2400.0,
    2000.0,
    1700.0,
    1600.0,
    1900.0,
    2050.0,
    2300.0,
    1500.0,
    1350.0,
    1250.0,
    1150.0,
]

BAR_COUNT = len(CLOSES)


def tail(series, count=4):
    """The last `count` entries -- what most expected-value assertions compare,
    since that is where every indicator has finished warming up."""
    return series[-count:]


def assert_series(actual, *, first_index, first, tail_values, length=BAR_COUNT, trailing_none=0):
    """One assertion for the three things every aligned series must get right.

    Alignment (same length as the input, so `series[-1]` is "now" and
    `series[-2]` is "the previous candle"), where the warm-up ends (an
    off-by-one here is the difference between a correct indicator and a
    plausible wrong one), and the actual numbers.
    """
    import pytest

    assert len(actual) == length
    assert actual[:first_index] == [None] * first_index, "warm-up ends at the wrong index"
    settled = actual[first_index : length - trailing_none]
    assert all(x is not None for x in settled), "a settled series must have no holes"
    if trailing_none:
        assert actual[length - trailing_none :] == [None] * trailing_none
    assert actual[first_index] == pytest.approx(first, rel=1e-6, abs=1e-6)
    if tail_values:
        end = length - trailing_none
        window = actual[end - len(tail_values) : end]
        assert window == pytest.approx(tail_values, rel=1e-6, abs=1e-6)
