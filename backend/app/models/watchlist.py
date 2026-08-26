from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.enums import DataSource
from app.models.mixins import TimestampMixin


class WatchlistItem(TimestampMixin, Base):
    """One symbol the owner wants on the dashboard.

    In the database rather than localStorage, which is where it lived: the
    quote table is the first thing they look at each morning, and it was empty
    on their phone, empty on a second computer, and gone after clearing
    browsing data -- with nothing on screen ever saying it only existed on
    that one machine.
    """

    __tablename__ = "watchlist_items"
    __table_args__ = (UniqueConstraint("user_id", "symbol", name="uq_watchlist_user_symbol"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    # Travels with the symbol: BTCUSDT priced off yfinance comes back empty,
    # and the list is the only place that knows which feed it belongs to.
    data_source: Mapped[DataSource] = mapped_column(
        SAEnum(DataSource, native_enum=False, length=32), default=DataSource.YFINANCE
    )
