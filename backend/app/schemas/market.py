from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import DataSource
from app.schemas.common import MoneyStr, UtcDatetime
from app.services.market_data.base import Timeframe
from app.services.market_data.service import DEFAULT_BAR_LIMIT


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
    # Optional, because Bar.volume is. The provider's NaN guard covers OHLC
    # only, so a row Yahoo padded over a halt really can arrive with no volume
    # -- and declaring it required turned that one row into a 500 on the chart.
    volume: Decimal | None = None


class BarsRead(BaseModel):
    """The candles, plus what was actually answered.

    The timeframe is echoed because a chart drawing weekly candles under a
    「日」 label is a wrong chart that looks right -- the failure this whole area
    keeps producing.
    """

    symbol: str
    timeframe: str
    bars: list[BarRead]
    # Whether the provider could not be reached, as opposed to answering with
    # nothing. The page needs different words for the two: 「no history」 is
    # permanent and 「could not fetch」 clears on its own, and showing the first
    # for the second is how a stock with fifty years of candles reads as
    # delisted.
    fetch_failed: bool = False


class IndicatorPointRead(BaseModel):
    time: UtcDatetime
    value: float


class IndicatorSeriesRead(BaseModel):
    name: str
    # Empty for an indicator with a single output. Present so a caller can tell
    # macd's three lines apart without parsing a composite name.
    key: str
    # "price" (shares the candles' axis) or "own". Decided on the server: see
    # services/indicator_panes.py for why it cannot be derived, and why a
    # second answer on the client would silently squash the chart.
    pane: str
    # Two series in one pane sharing this string share an axis. Everything an
    # indicator returns shares it by default -- macd against its own signal
    # line is the point of macd -- except where the server declares otherwise.
    scale: str
    points: list[IndicatorPointRead]


class IndicatorsRead(BaseModel):
    symbol: str
    timeframe: str
    series: list[IndicatorSeriesRead]


class IndicatorRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    timeframe: Timeframe = Timeframe.DAY_1
    # Matches GET /bars and the market loop, so all three share one cache
    # entry and one upstream fetch. See the comment on that endpoint.
    limit: int = Field(default=DEFAULT_BAR_LIMIT, ge=1, le=1000)
    data_source: DataSource = DataSource.YFINANCE
    indicators: list["IndicatorSpecRequest"] = Field(default_factory=list)


class IndicatorSpecRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    # Only the tuning knobs. The bar columns are bound from the candles the
    # server just fetched -- accepting them from the client would let a caller
    # draw an indicator over prices this app never saw.
    params: dict = Field(default_factory=dict)


class AvailableIndicators(BaseModel):
    indicators: list[dict]
