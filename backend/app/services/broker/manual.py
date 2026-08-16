import uuid
from decimal import Decimal

from app.models.order import Order
from app.services.broker.base import BrokerResult


class ManualConfirmBroker:
    """v1's only broker adapter: records exactly what the user says they did
    (they placed the order themselves through their own broker/trading
    software). No real order is ever transmitted anywhere by this class."""

    def submit(self, order: Order, fill_price: Decimal, fill_quantity: Decimal) -> BrokerResult:
        return BrokerResult(ok=True, ref=f"manual:{uuid.uuid4()}", fill_price=fill_price)
