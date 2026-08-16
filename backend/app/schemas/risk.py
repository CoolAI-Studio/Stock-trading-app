from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.schemas.common import MoneyStr


class RiskSettingsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    capital: MoneyStr
    stop_loss_pct: MoneyStr
    take_profit_pct: MoneyStr
    max_position_qty: MoneyStr
    max_order_notional: MoneyStr
    max_pending_orders_per_symbol: int
    signal_cooldown_sec: int


class RiskSettingsUpdate(BaseModel):
    capital: Decimal | None = None
    stop_loss_pct: Decimal | None = None
    take_profit_pct: Decimal | None = None
    max_position_qty: Decimal | None = None
    max_order_notional: Decimal | None = None
    max_pending_orders_per_symbol: int | None = None
    signal_cooldown_sec: int | None = None
