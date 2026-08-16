from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class RiskSettingsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    capital: Decimal
    stop_loss_pct: Decimal
    take_profit_pct: Decimal
    max_position_qty: Decimal
    max_order_notional: Decimal
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
