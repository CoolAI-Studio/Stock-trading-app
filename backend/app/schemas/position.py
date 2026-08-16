from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.schemas.common import MoneyStr


class PositionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    quantity: MoneyStr
    avg_entry_price: MoneyStr
    realized_pnl: MoneyStr
    opened_at: datetime | None


class PositionAdjust(BaseModel):
    quantity: Decimal
    avg_entry_price: Decimal
