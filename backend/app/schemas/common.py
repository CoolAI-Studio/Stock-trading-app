from decimal import Decimal
from typing import Annotated

from pydantic import PlainSerializer


def format_decimal(value: Decimal) -> str:
    """Fixed-point, trailing-zero-free string for any Decimal in an API
    response.

    Needed because SQLAlchemy's SQLite dialect hands back a zero-valued
    Numeric(18, 8) column as Decimal('0E-8') after a round trip -- which
    str()'s as scientific notation ("0E-8") instead of "0". `normalize()`
    alone doesn't fix this: it collapses trailing zeros correctly for
    zero, but format(..., 'f') is what keeps everything (zero and
    non-zero) in fixed-point notation rather than switching to exponent
    form for values normalize() would otherwise re-scale oddly.
    """
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


# Attach to any response-schema field typed as Decimal to get consistent,
# human-readable formatting instead of raw str(Decimal(...)).
MoneyStr = Annotated[Decimal, PlainSerializer(format_decimal, return_type=str, when_used="json")]
