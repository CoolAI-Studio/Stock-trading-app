from datetime import datetime
from decimal import Decimal

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import DataSource
from app.models.mixins import TimestampMixin


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
    default_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal(1))
    warmup_bars: Mapped[int] = mapped_column(default=30)

    last_signal: Mapped[str | None] = mapped_column(String(8), default=None)
    last_signal_at: Mapped[datetime | None] = mapped_column(default=None)
    last_run_at: Mapped[datetime | None] = mapped_column(default=None)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    consecutive_errors: Mapped[int] = mapped_column(default=0)
