from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_validator

from app.schemas.common import UtcDatetime


class TradingViewAlert(BaseModel):
    secret: str
    symbol: str
    # TradingView's {{exchange}} placeholder, when the owner puts it in the
    # alert message. Optional because every alert configured before it was
    # asked for sends nothing -- and refusing those would drop every one of
    # them, which is strictly worse than the ambiguity it guards against.
    exchange: str | None = None
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


class TradingViewWebhookLogRead(BaseModel):
    """One recorded call. Failures are included deliberately -- a wrong secret
    or malformed JSON is precisely the row the owner is looking for."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    received_at: UtcDatetime
    remote_ip: str | None
    signature_valid: bool
    parsed_ok: bool
    raw_body: str
    order_id: int | None
    error: str | None
    # No `id` on the alert, so it is only covered by the identical-body window
    # rather than being properly idempotent. Surfaced so the owner learns it
    # from the page rather than from being replayed.
    missing_id: bool


class TradingViewSetup(BaseModel):
    """What to paste into TradingView. Served rather than documented, because
    a URL in a docs page is a URL nobody finds."""

    url: str
    example_message: str
    notes: list[str]
