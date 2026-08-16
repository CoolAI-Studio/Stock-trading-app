from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from app.models.order import Order


@dataclass
class BrokerResult:
    ok: bool
    ref: str
    fill_price: Decimal
    error: str | None = None


class BrokerAdapter(Protocol):
    """Confirming an order always goes through a BrokerAdapter, never a
    direct DB write -- so swapping the v1 ManualConfirmBroker for a real
    broker integration later is a one-function change (the factory that
    picks which adapter to use), not a rewrite of the orders router."""

    def submit(self, order: Order, fill_price: Decimal, fill_quantity: Decimal) -> BrokerResult: ...
