from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import DataSource
from app.schemas.common import UtcDatetime


class WatchlistItemCreate(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    data_source: DataSource = DataSource.YFINANCE

    @field_validator("symbol")
    @classmethod
    def _normalise(cls, value: str) -> str:
        """Uppercased and trimmed, because that is what the providers expect
        and a lowercase ticker silently resolves to nothing at all."""
        cleaned = value.strip().upper()
        if not cleaned:
            raise ValueError("symbol cannot be blank")
        return cleaned


class WatchlistItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    data_source: DataSource
    created_at: UtcDatetime
