from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from app.models.enums import DataSource


@dataclass
class Quote:
    symbol: str
    data_source: DataSource
    price: Decimal
    prev_close: Decimal | None = None
    change_pct: Decimal | None = None
    volume: Decimal | None = None
    quote_time: datetime | None = None


class QuoteProvider(Protocol):
    data_source: DataSource

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]: ...
