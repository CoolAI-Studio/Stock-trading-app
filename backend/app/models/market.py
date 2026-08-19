from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import DataSource
from app.models.mixins import utcnow


class MarketQuote(Base):
    """One upserted row per symbol -- not an append-only tick log. At a 5s
    poll interval an append-only table would write ~17k rows/symbol/day for
    data the TradingView widget already displays; skip that in v1."""

    __tablename__ = "market_quotes"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    data_source: Mapped[DataSource] = mapped_column(
        SAEnum(DataSource, native_enum=False, length=32)
    )
    price: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    prev_close: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), default=None)
    change_pct: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), default=None)
    volume: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), default=None)
    quote_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
