from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.enums import DataSource
from app.schemas.common import MoneyStr, UtcDatetime


class QuoteRead(BaseModel):
    symbol: str
    data_source: DataSource
    price: MoneyStr
    prev_close: MoneyStr | None = None
    change_pct: MoneyStr | None = None
    volume: MoneyStr | None = None
    quote_time: UtcDatetime | None = None
    # So the screen can label the number instead of leaving the owner to
    # remember which market this row came from.
    currency: str | None = None


class BarRead(BaseModel):
    """One candle, in the shape a charting library reads.

    `time` is the candle's OPEN time, which is what every chart plots against
    and what backtest.py replays on. Serialized as an ISO string like every
    other timestamp in this API rather than as an epoch number: the frontend
    converts once, and a bare integer in a JSON body is the kind of field
    somebody later reads as milliseconds.
    """

    model_config = ConfigDict(from_attributes=True)

    time: UtcDatetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


class BarsRead(BaseModel):
    """The candles, plus what was actually answered.

    The timeframe is echoed because a chart drawing weekly candles under a
    「日」 label is a wrong chart that looks right -- the failure this whole area
    keeps producing.
    """

    symbol: str
    timeframe: str
    bars: list[BarRead]
