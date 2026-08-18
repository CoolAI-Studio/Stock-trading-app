from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import NotificationStatus, OrderSide
from app.schemas.common import MoneyStr


class StrategyAlertRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    strategy_id: int
    symbol: str
    side: OrderSide
    price: MoneyStr
    # A FAILED row is still a signal the strategy produced -- it counts when
    # judging the strategy, and it is the only place the owner can see that
    # a notification channel stopped working.
    status: NotificationStatus
    error: str | None
    created_at: datetime
