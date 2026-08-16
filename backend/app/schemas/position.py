from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class PositionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    quantity: Decimal
    avg_entry_price: Decimal
    realized_pnl: Decimal
    opened_at: datetime | None


class PositionAdjust(BaseModel):
    quantity: Decimal
    avg_entry_price: Decimal
