"""Every timestamp the API emits has to say which zone it is in.

A timestamp serialized as "2026-08-19T01:30:00" with no offset is read by
`new Date(...)` in the browser as *local* time. The value really is UTC, so a
position opened at 09:30 in Taipei renders as 01:30 the same morning -- eight
hours early, silently, and only on some pages, which is what made it look like
the system was jumping around rather than like a type declaration being wrong.
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import DateTime

import app.models  # noqa: F401  -- registers every table on Base.metadata
from app.db.base import Base
from app.models.enums import OrderSide, OrderSource, OrderStatus
from app.models.order import Order
from app.models.position import Position


def test_no_column_stores_a_timestamp_without_its_timezone():
    """Structural, because the failure is invisible per-column: a bare
    `Mapped[datetime]` maps to TIMESTAMP WITHOUT TIME ZONE, Postgres drops the
    offset on write, and the next column somebody adds does it again."""
    naive = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if isinstance(column.type, DateTime) and not column.type.timezone
    ]
    assert naive == [], f"these columns lose their timezone on write: {naive}"


def test_a_position_reports_when_it_was_opened_in_utc(auth_client, db_session):
    opened = datetime(2026, 8, 19, 1, 30, tzinfo=UTC)
    db_session.add(
        Position(
            user_id=1,
            symbol="2330.TW",
            quantity=Decimal(1000),
            avg_entry_price=Decimal(1000),
            opened_at=opened,
        )
    )
    db_session.commit()

    body = auth_client.get("/api/positions").json()
    assert len(body) == 1
    # Not a format preference: without the offset the browser reads this as
    # 01:30 Taipei, and the position was opened at 09:30 Taipei.
    assert body[0]["opened_at"].endswith(("Z", "+00:00"))


def test_an_orders_fill_and_decision_times_are_reported_in_utc(auth_client, db_session):
    stamp = datetime(2026, 8, 19, 1, 30, tzinfo=UTC)
    db_session.add(
        Order(
            user_id=1,
            source=OrderSource.MANUAL,
            symbol="AAPL",
            side=OrderSide.BUY,
            quantity=Decimal(1),
            status=OrderStatus.CONFIRMED,
            fill_price=Decimal(100),
            filled_quantity=Decimal(1),
            filled_at=stamp,
            decided_at=stamp,
        )
    )
    db_session.commit()

    body = auth_client.get("/api/orders").json()
    assert len(body) == 1
    assert body[0]["filled_at"].endswith(("Z", "+00:00"))
    assert body[0]["decided_at"].endswith(("Z", "+00:00"))
