from pydantic import BaseModel

from app.models.enums import DataSource
from app.schemas.common import MoneyStr, UtcDatetime


class QuoteRead(BaseModel):
    symbol: str
    data_source: DataSource
    price: MoneyStr
    prev_close: MoneyStr | None = None
    change_pct: MoneyStr | None = None
    volume: MoneyStr | None = None
    quote_time: UtcDatetime | None = None
    # So the screen can label the number instead of leaving the owner to
    # remember which market this row came from.
    currency: str | None = None
