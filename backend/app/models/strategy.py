from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import DataSource, NotificationStatus, OrderSide
from app.models.mixins import TimestampMixin, utcnow

# Generic fallback for source that declares no `self.warmup_bars`. Named
# rather than inlined because a backtest of *draft* source has no strategies
# row to read it from, and the two must not drift apart.
DEFAULT_WARMUP_BARS = 30


class Strategy(TimestampMixin, Base):
    __tablename__ = "strategies"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_strategies_user_id_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    name: Mapped[str] = mapped_column(String(120))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    data_source: Mapped[DataSource] = mapped_column(
        SAEnum(DataSource, native_enum=False, length=32), default=DataSource.YFINANCE
    )

    # Stored as text, not a file on disk -- cloud hosts (Render/Fly without a
    # volume) have ephemeral disks; a redeploy would silently wipe on-disk
    # strategy files.
    source_code: Mapped[str] = mapped_column(Text)
    code_hash: Mapped[str] = mapped_column(String(64))

    is_active: Mapped[bool] = mapped_column(default=False)
    # Watch-only: BUY/SELL notifies and is recorded as a StrategyAlert, but
    # never becomes a pending order. Off for every existing strategy.
    alert_only: Mapped[bool] = mapped_column(default=False)
    default_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal(1))
    warmup_bars: Mapped[int] = mapped_column(default=DEFAULT_WARMUP_BARS)

    # Per-strategy risk overrides. NULL means "inherit the user's global
    # RiskSettings value" -- resolved in services/risk_resolver.py, which is
    # the only place that coalescing happens. Every strategy that predates
    # these columns holds NULL, so it keeps behaving exactly as it did.
    capital: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), default=None)
    stop_loss_pct: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), default=None)
    take_profit_pct: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), default=None)
    max_position_qty: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), default=None)
    max_order_notional: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), default=None)
    max_pending_orders_per_symbol: Mapped[int | None] = mapped_column(default=None)
    signal_cooldown_sec: Mapped[int | None] = mapped_column(default=None)
    alert_interval_sec: Mapped[int | None] = mapped_column(default=None)

    last_signal: Mapped[str | None] = mapped_column(String(8), default=None)
    last_signal_at: Mapped[datetime | None] = mapped_column(default=None)
    last_run_at: Mapped[datetime | None] = mapped_column(default=None)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    consecutive_errors: Mapped[int] = mapped_column(default=0)


class StrategyAlert(Base):
    """One row per alert *attempt* made by an alert-only strategy.

    Failed attempts are kept rather than dropped: the throttle clock in
    services/alerts.py only starts from a delivered alert, and the retry
    bound counts the failures recorded since that delivery. Keeping them
    also tells the owner why their phone went quiet -- a run of FAILED rows
    is a broken channel, not a strategy that stopped firing.
    """

    __tablename__ = "strategy_alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    strategy_id: Mapped[int] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), index=True
    )

    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[OrderSide] = mapped_column(SAEnum(OrderSide, native_enum=False, length=8))
    # The quote the strategy actually saw, so a past alert can be scored
    # against what the price did afterwards.
    price: Mapped[Decimal] = mapped_column(Numeric(18, 8))

    status: Mapped[NotificationStatus] = mapped_column(
        SAEnum(NotificationStatus, native_enum=False, length=16)
    )
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
