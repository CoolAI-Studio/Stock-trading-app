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
    # What `price` is denominated in. A bare number was safe only while symbol
    # search could emit .TW/.TWO and US tickers alone; now that a US ADR and
    # its Taiwanese line can both answer 「台積電」, NT$2,375 and US$300 land in
    # the same column and a threshold typed against one is silently wrong
    # against the other. NULL on rows written before this existed.
    currency: Mapped[str | None] = mapped_column(String(8), default=None)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MarketBar(Base):
    """一根收好的 K 棒，存下來是為了重開機之後圖表還畫得出來。

    報價早就有自己的表，K 棒沒有——而 Render 的免費方案閒置就休眠，所以每次醒來
    都得重新跟上游要一次，要不到就是一張空圖。那個不對稱就是「圖表在線上很不可
    靠」的結構原因。

    APPEND-ONLY 是錯的，這一點值得寫下來：provider 回的是還原價，所以一次分割會
    讓上游把整段歷史改寫。只增不改會在分割那天接出一根不存在的長黑，而策略會照
    著它算——見 services/bar_store.py 的重疊比對。

    鍵是四個欄位一起：同一個代號在不同來源、不同週期是不同的序列，混在一起就是
    把日線接到分線後面。
    """

    __tablename__ = "market_bars"

    data_source: Mapped[DataSource] = mapped_column(
        SAEnum(DataSource, native_enum=False, length=32), primary_key=True
    )
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(8), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    open: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    high: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    low: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    close: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    volume: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), default=None)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
