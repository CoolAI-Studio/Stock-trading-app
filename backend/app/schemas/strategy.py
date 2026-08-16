from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import DataSource


class StrategyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    symbol: str = Field(min_length=1, max_length=32)
    data_source: DataSource = DataSource.YFINANCE
    source_code: str = Field(min_length=1)
    default_quantity: Decimal = Decimal(1)
    warmup_bars: int = Field(default=30, ge=0)


class StrategyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    symbol: str | None = Field(default=None, min_length=1, max_length=32)
    data_source: DataSource | None = None
    source_code: str | None = Field(default=None, min_length=1)
    default_quantity: Decimal | None = None
    warmup_bars: int | None = Field(default=None, ge=0)


class StrategyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    symbol: str
    data_source: DataSource
    is_active: bool
    default_quantity: Decimal
    warmup_bars: int
    last_signal: str | None
    last_signal_at: datetime | None
    last_run_at: datetime | None
    last_error: str | None
    consecutive_errors: int


class StrategyValidateRequest(BaseModel):
    source_code: str = Field(min_length=1)
    sample_prices: list[float] | None = None


class StrategyValidateResult(BaseModel):
    ok: bool
    error: str | None = None
    detected_name: str | None = None
    detected_symbol: str | None = None
    sample_signals: list[str] | None = None


class SampleStrategyInfo(BaseModel):
    filename: str
    source_code: str
