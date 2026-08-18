from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import DataSource
from app.schemas.common import MoneyStr


class StrategyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    symbol: str = Field(min_length=1, max_length=32)
    data_source: DataSource = DataSource.YFINANCE
    source_code: str = Field(min_length=1)
    default_quantity: Decimal = Decimal(1)
    warmup_bars: int = Field(default=30, ge=0)
    alert_only: bool = False


class StrategyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    symbol: str | None = Field(default=None, min_length=1, max_length=32)
    data_source: DataSource | None = None
    source_code: str | None = Field(default=None, min_length=1)
    default_quantity: Decimal | None = None
    warmup_bars: int | None = Field(default=None, ge=0)
    alert_only: bool | None = None


class StrategyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    symbol: str
    data_source: DataSource
    is_active: bool
    alert_only: bool
    default_quantity: MoneyStr
    warmup_bars: int
    last_signal: str | None
    last_signal_at: datetime | None
    last_run_at: datetime | None
    last_error: str | None
    consecutive_errors: int


class StrategyDetail(StrategyRead):
    """Single-strategy read. Carries the source; the list response
    deliberately does not, since the dashboard polls it and would otherwise
    ship every strategy's full text on every poll. The edit form loads this
    one -- opening that editor blank is indistinguishable from the code
    having been lost, and saving from there would then lose it for real.
    """

    source_code: str


class StrategyValidateRequest(BaseModel):
    source_code: str = Field(min_length=1)
    sample_prices: list[float] | None = None


class StrategyValidateResult(BaseModel):
    ok: bool
    error: str | None = None
    detected_name: str | None = None
    detected_symbol: str | None = None
    # Which entry point the source turned out to use, "on_tick" or "on_bar".
    # Reported because the two read almost alike, and a strategy that quietly
    # got the wrong one looks exactly like a strategy that works.
    entry_point: str | None = None
    # The candle size an on_bar strategy declared; None for on_tick, which
    # has no candles to declare.
    timeframe: str | None = None
    sample_signals: list[str] | None = None


class StrategyGenerateRequest(BaseModel):
    description: str = Field(min_length=1, max_length=4000)
    symbol: str | None = Field(default=None, max_length=32)


class StrategyGenerateResult(StrategyValidateResult):
    """Everything /validate returns plus the code it describes, so one round
    trip is enough for the editor to fill in the source and show
    "偵測到：名稱（代號）". source_code is None only when generation never got
    as far as producing any."""

    source_code: str | None = None


class SampleStrategyInfo(BaseModel):
    filename: str
    source_code: str
