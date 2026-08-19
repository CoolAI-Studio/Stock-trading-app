from pydantic import BaseModel, ConfigDict

from app.schemas.common import MoneyStr


class BrokerCostPresetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    label: str
    # TW / US / CRYPTO -- lets the form group them and warn when the chosen
    # preset does not match the symbol being tested.
    market: str
    commission_rate: MoneyStr
    minimum_fee: MoneyStr
    sell_tax_rate: MoneyStr
    # What this preset assumes, in the owner's language. Discount tiers vary
    # per customer, so the note is the part that stops a preset being read as
    # a promise.
    note: str
