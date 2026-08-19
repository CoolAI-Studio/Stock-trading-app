from decimal import Decimal

from app.services import risk


def test_stop_loss_triggered_when_price_drops_below_threshold():
    assert risk.check_stop_loss(Decimal(100), Decimal(90), Decimal("0.1")) is True
    assert risk.check_stop_loss(Decimal(100), Decimal(95), Decimal("0.1")) is False


def test_take_profit_triggered_when_price_rises_above_threshold():
    assert risk.check_take_profit(Decimal(100), Decimal(110), Decimal("0.1")) is True
    assert risk.check_take_profit(Decimal(100), Decimal(105), Decimal("0.1")) is False


def test_take_profit_handles_zero_entry_price_without_dividing_by_zero():
    assert risk.check_take_profit(Decimal(0), Decimal(10), Decimal("0.1")) is False


def test_position_limit_allows_under_the_cap():
    # ported verbatim from the legacy RiskControl: hitting the cap exactly is
    # treated as exceeding it, not as a pass (strict `<`, not `<=`).
    assert risk.check_position_limit(Decimal(20), Decimal(25), Decimal(50)) is True


def test_position_limit_rejects_at_or_over_the_cap():
    assert risk.check_position_limit(Decimal(20), Decimal(30), Decimal(50)) is False
    assert risk.check_position_limit(Decimal(0), Decimal(50), Decimal(50)) is False


def test_position_limit_unconfigured_cap_means_no_limit():
    # max_position_qty defaults to 0 for a brand-new user's risk settings --
    # that must mean "not configured yet", not "block everything forever".
    assert risk.check_position_limit(Decimal(1000), Decimal(1000), Decimal(0)) is True


# ---- 0 means "off", uniformly across every risk knob ----
# Five of the eight knobs already read 0 as "no limit". These are two of the
# three that used to read it as a trap instead, so the UI can offer one
# "不限制" switch rather than eight special cases.


def test_stop_loss_zero_pct_means_no_stop_loss():
    # 0 used to mean "sell the instant price touches cost", and again on the
    # way down -- the opposite of the "off" the owner expects to type.
    assert risk.check_stop_loss(Decimal(100), Decimal(100), Decimal(0)) is False
    assert risk.check_stop_loss(Decimal(100), Decimal(1), Decimal(0)) is False
    # ...while a configured stop still fires exactly where it used to.
    assert risk.check_stop_loss(Decimal(100), Decimal(90), Decimal("0.1")) is True


def test_take_profit_zero_pct_means_no_take_profit():
    # 0 used to mean "sell the instant price returns to cost", and on every
    # gain above it.
    assert risk.check_take_profit(Decimal(100), Decimal(100), Decimal(0)) is False
    assert risk.check_take_profit(Decimal(100), Decimal(1000), Decimal(0)) is False
    assert risk.check_take_profit(Decimal(100), Decimal(110), Decimal("0.1")) is True
