from decimal import Decimal

from app.schemas.common import format_decimal


def test_zero_from_sqlite_numeric_roundtrip_formats_as_plain_zero():
    # This is exactly what SQLAlchemy's SQLite dialect hands back for a
    # zero-valued Numeric(18, 8) column after a round trip -- Decimal('0'),
    # scaled to Decimal('0E-8'), which str()'s as ugly scientific notation.
    assert format_decimal(Decimal("0E-8")) == "0"


def test_plain_zero_formats_as_plain_zero():
    assert format_decimal(Decimal(0)) == "0"


def test_negative_zero_formats_as_plain_zero():
    assert format_decimal(Decimal("-0.00000000")) == "0"


def test_normal_value_drops_trailing_zeros_without_scientific_notation():
    assert format_decimal(Decimal("150.50000000")) == "150.5"


def test_integer_value_has_no_decimal_point():
    assert format_decimal(Decimal("1000000.00000000")) == "1000000"


def test_preserves_significant_fractional_digits():
    assert format_decimal(Decimal("97.14550000")) == "97.1455"


def test_risk_settings_api_returns_plain_zero_not_scientific_notation(auth_client):
    resp = auth_client.get("/api/risk-settings")
    assert resp.status_code == 200
    assert resp.json()["max_position_qty"] == "0"
    assert "E-8" not in resp.text
