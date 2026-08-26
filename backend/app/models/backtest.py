from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.enums import DataSource
from app.models.mixins import utcnow


class BacktestRun(Base):
    """One completed replay, kept so its numbers can be revisited.

    A run is immutable -- it records what happened when it was executed -- so
    it carries only created_at rather than the TimestampMixin's created/updated
    pair.

    It also carries its own SNAPSHOT of everything it depended on: the source
    code, the symbol, the date range and the cost assumptions. That redundancy
    is the whole point. A saved strategy gets edited, and the moment it does, a
    stored run that merely pointed at strategies.source_code would silently
    start describing code it never scored -- numbers that look like evidence
    for the wrong thing, which is the most convincing way to be wrong.
    """

    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # Nullable, and nulled explicitly when the strategy is deleted (see the
    # comment in api/routers/strategies.py::delete_strategy for why the
    # ondelete clause alone is not enough on SQLite). The run stays readable
    # either way -- that is what strategy_name and source_code below are for.
    strategy_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"), default=None, index=True
    )
    strategy_name: Mapped[str] = mapped_column(String(120))

    symbol: Mapped[str] = mapped_column(String(32))
    # Plain text rather than SAEnum(Timeframe): Timeframe lives in
    # services/market_data, and a model reaching into the services layer to
    # spell a column type is a dependency the schema does not need.
    timeframe: Mapped[str] = mapped_column(String(8))
    data_source: Mapped[DataSource] = mapped_column(
        SAEnum(DataSource, native_enum=False, length=32)
    )
    range_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    range_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    source_code: Mapped[str] = mapped_column(Text)
    code_hash: Mapped[str] = mapped_column(String(64))

    # Already-serialized JSON, exactly as the API returned it. Stored that way
    # rather than as reconstructable pieces so a later change to the response
    # shape cannot silently reinterpret an old run: what the owner reads back
    # is what they were shown at the time.
    assumptions: Mapped[dict] = mapped_column(JSON)
    # Duplicated out of `result` so the list view can show every run's headline
    # numbers without shipping every run's full equity curve with them.
    summary: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
