from decimal import Decimal

from pydantic import BaseModel, field_validator


class TradingViewAlert(BaseModel):
    secret: str
    symbol: str
    action: str
    quantity: Decimal | None = None
    price: Decimal | None = None
    strategy: str | None = None
    id: str | None = None

    @field_validator("action")
    @classmethod
    def normalize_action(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ("buy", "sell"):
            raise ValueError("action must be 'buy' or 'sell'")
        return normalized
