from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.models.enums import DataSource


class QuoteRead(BaseModel):
    symbol: str
    data_source: DataSource
    price: Decimal
    prev_close: Decimal | None = None
    change_pct: Decimal | None = None
    volume: Decimal | None = None
    quote_time: datetime | None = None
