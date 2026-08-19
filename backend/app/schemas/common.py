from datetime import UTC, datetime
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


def stamp_utc(value: datetime) -> datetime:
    """Attach UTC to a timestamp that lost it on the way out of the database.

    Every timestamp this app writes is UTC. Whether it still says so by the
    time it reaches a response depends on the dialect: Postgres round-trips a
    TIMESTAMPTZ with its offset intact, but SQLite's DATETIME has no timezone
    concept at all and hands back a naive value even from a
    `DateTime(timezone=True)` column. Serialized naive, the browser's
    `new Date(...)` reads it as *local* time -- eight hours early in Taipei,
    and only on the pages whose columns happened to be declared without a
    timezone, which is what made it look like the clock was jumping.

    So the offset is asserted here, at the API boundary where the contract
    lives, rather than trusted to whichever database is underneath.
    """
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


# Attach to any response-schema field typed as datetime. Same role as MoneyStr:
# one place that makes the wire format consistent across both backends.
UtcDatetime = Annotated[datetime, PlainSerializer(stamp_utc, return_type=datetime)]
