from datetime import datetime

from pydantic import BaseModel

from app.models.enums import DataSource
from app.schemas.common import MoneyStr


class QuoteRead(BaseModel):
    symbol: str
    data_source: DataSource
    price: MoneyStr
    prev_close: MoneyStr | None = None
    change_pct: MoneyStr | None = None
    volume: MoneyStr | None = None
    quote_time: datetime | None = None
