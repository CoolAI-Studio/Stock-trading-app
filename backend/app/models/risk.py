from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class RiskSettings(TimestampMixin, Base):
    """One row per user. Direct port of the legacy RiskControl.__init__ knobs
    (capital / stop_loss_pct / take_profit_pct / max_position_qty), plus v1
    additions for gating the signal->pending-order pipeline."""

    __tablename__ = "risk_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)

    capital: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal(0))
    stop_loss_pct: Mapped[Decimal] = mapped_column(Numeric(9, 6), default=Decimal("0.05"))
    take_profit_pct: Mapped[Decimal] = mapped_column(Numeric(9, 6), default=Decimal("0.10"))
    max_position_qty: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal(0))
    max_order_notional: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal(0))
    max_pending_orders_per_symbol: Mapped[int] = mapped_column(default=3)
    signal_cooldown_sec: Mapped[int] = mapped_column(default=300)
