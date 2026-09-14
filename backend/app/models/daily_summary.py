from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class DailySummary(TimestampMixin, Base):
    """收盤後一則的自選股摘要，一個帳號一列（ONBOARDING.md 方案 2，#117）。

    **清單不存在這裡。** 摘要的就是自選股——引導做出來的東西是普通資料（ONBOARDING.md 通
    則 6），之後在自選股那裡照常增刪——所以這一列只有開關和執行紀錄。

    `*_done_on` 是那個市場**當地**的交易日，不是 UTC 日期，也不是他所在地的日期：美股收盤是
    台北時間的隔天凌晨，而「同一天不重送」說的是紐約的那一天。
    """

    __tablename__ = "daily_summaries"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    tw_done_on: Mapped[date | None] = mapped_column(Date, default=None)
    us_done_on: Mapped[date | None] = mapped_column(Date, default=None)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # 最後一次沒送成的原因（例如「今天還拿不到收盤資料」）。送成功就清掉——一個已經
    # 過去的問題還留在畫面上，會讓他去修一件已經好了的事。
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
