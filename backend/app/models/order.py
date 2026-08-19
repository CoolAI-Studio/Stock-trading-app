from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import OrderSide, OrderSource, OrderStatus
from app.models.mixins import TimestampMixin


class Order(TimestampMixin, Base):
    """A trade signal *and* the pending manual-confirm order in one row --
    there's no separate execution table in v1 since nothing auto-executes."""

    __tablename__ = "orders"
    __table_args__ = (Index("ix_orders_user_status_created", "user_id", "status", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    strategy_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"), default=None
    )

    source: Mapped[OrderSource] = mapped_column(SAEnum(OrderSource, native_enum=False, length=16))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[OrderSide] = mapped_column(SAEnum(OrderSide, native_enum=False, length=8))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    signal_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), default=None)

    status: Mapped[OrderStatus] = mapped_column(
        SAEnum(OrderStatus, native_enum=False, length=16), default=OrderStatus.PENDING
    )
    risk_notes: Mapped[dict | None] = mapped_column(JSON, default=None)
    reject_reason: Mapped[str | None] = mapped_column(Text, default=None)

    fill_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), default=None)
    # How much actually filled, which is not always `quantity`: confirming an
    # order may name a smaller amount, and that smaller amount is what reaches
    # the position. Without it recorded, the order row overstates what changed
    # hands and anything totalling spend from order history is wrong.
    filled_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), default=None)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    broker_ref: Mapped[str | None] = mapped_column(String(128), default=None)

    # Stops a retried TradingView alert (or a double-click) from creating two
    # orders for the same signal.
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, default=None)
    raw_payload: Mapped[dict | None] = mapped_column(JSON, default=None)
