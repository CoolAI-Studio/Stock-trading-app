from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import OrderSide, OrderSource, OrderStatus
from app.schemas.common import MoneyStr


class OrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    strategy_id: int | None
    source: OrderSource
    symbol: str
    side: OrderSide
    quantity: MoneyStr
    signal_price: MoneyStr | None
    status: OrderStatus
    risk_notes: dict | None
    reject_reason: str | None
    fill_price: MoneyStr | None
    filled_quantity: MoneyStr | None
    filled_at: datetime | None
    decided_at: datetime | None
    broker_ref: str | None
    created_at: datetime


class ManualOrderCreate(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    side: OrderSide
    quantity: Decimal = Field(gt=0)
    signal_price: Decimal | None = None


class OrderConfirmRequest(BaseModel):
    fill_price: Decimal = Field(gt=0)
    quantity: Decimal | None = Field(default=None, gt=0)


class OrderRejectRequest(BaseModel):
    reason: str | None = None
