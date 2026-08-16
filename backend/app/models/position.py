from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class Position(TimestampMixin, Base):
    __tablename__ = "positions"
    __table_args__ = (UniqueConstraint("user_id", "symbol", name="uq_positions_user_id_symbol"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    symbol: Mapped[str] = mapped_column(String(32), index=True)

    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal(0))
    avg_entry_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal(0))
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal(0))
    opened_at: Mapped[datetime | None] = mapped_column(default=None)
