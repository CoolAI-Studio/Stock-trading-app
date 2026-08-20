from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import DataSource
from app.schemas.common import UtcDatetime
from app.services import symbol_search


class WatchlistItemCreate(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    data_source: DataSource = DataSource.YFINANCE

    @field_validator("symbol")
    @classmethod
    def _normalise(cls, value: str) -> str:
        """Trimmed, upper-cased, and refused outright when it cannot ever
        produce a price.

        The refusal is the important half. `.upper()` is a no-op on Chinese, so
        「台積電」 used to sail through, get stored, and then be asked for on every
        poll -- no quote, no row on the dashboard, no error anywhere, and no
        alert from a watchlist entry that looked perfectly fine. A bare 「2330」
        was worse: Yahoo resolves it to an unrelated Japanese OTC company, so
        it priced, and the owner would have been watching the wrong stock.
        """
        cleaned = symbol_search.normalise(value)
        if not cleaned:
            raise ValueError("請輸入股票代號。")
        problem = symbol_search.looks_unpriceable(cleaned)
        if problem:
            raise ValueError(problem)
        return cleaned

    @model_validator(mode="after")
    def _check_source_matches_symbol(self):
        """A row whose source cannot price its symbol is a row that shows a
        blank price forever, with nothing anywhere saying why."""
        problem = symbol_search.market_mismatch(self.symbol, self.data_source)
        if problem:
            raise ValueError(problem)
        return self


class WatchlistItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    data_source: DataSource
    created_at: UtcDatetime
