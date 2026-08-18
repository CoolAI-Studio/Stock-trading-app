from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import MoneyStr


class PositionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    quantity: MoneyStr
    avg_entry_price: MoneyStr
    realized_pnl: MoneyStr
    opened_at: datetime | None
    # Which strategy opened this position, and therefore whose stop-loss /
    # take-profit thresholds it is being scanned under. None means the global
    # settings apply. Surfaced so the owner can see that rather than guess it.
    strategy_id: int | None


class PositionAdjust(BaseModel):
    # Non-negative: this app has no short-selling concept, and a negative
    # quantity here used to corrupt the cost basis on the next buy. Sell via
    # an order, or use DELETE /positions/{symbol} to flatten.
    quantity: Decimal = Field(ge=0)
    avg_entry_price: Decimal = Field(ge=0)
