from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import DataSource
from app.schemas.common import MoneyStr
from app.services.backtest import (
    DEFAULT_COMMISSION_RATE,
    DEFAULT_INITIAL_CAPITAL,
    DEFAULT_QUANTITY,
    DEFAULT_SELL_TAX_RATE,
    DEFAULT_SLIPPAGE_RATE,
    BacktestAssumptions,
    FillPriceBasis,
)
from app.services.market_data.base import Timeframe


class BacktestAssumptionsRead(BaseModel):
    """What the simulation charged for. Echoed back on every response because
    a return figure without its costs is not a number the owner can act on."""

    model_config = ConfigDict(from_attributes=True)

    fill_price_basis: FillPriceBasis
    commission_rate: MoneyStr
    slippage_rate: MoneyStr
    sell_tax_rate: MoneyStr
    quantity: MoneyStr
    initial_capital: MoneyStr


class BacktestRunRequest(BaseModel):
    """Either a saved strategy or a draft's source, plus the range and the
    assumptions to run it under."""

    strategy_id: int | None = None
    source_code: str | None = Field(default=None, min_length=1)
    # Optional overrides. symbol defaults to the strategy's own; timeframe is
    # only accepted for an on_tick strategy -- see the router for why.
    symbol: str | None = Field(default=None, min_length=1, max_length=32)
    timeframe: Timeframe | None = None
    data_source: DataSource | None = None
    start: datetime
    end: datetime
    warmup_bars: int | None = Field(default=None, ge=0)

    fill_price_basis: FillPriceBasis = FillPriceBasis.NEXT_OPEN
    commission_rate: Decimal = Field(default=DEFAULT_COMMISSION_RATE, ge=0, le=1)
    slippage_rate: Decimal = Field(default=DEFAULT_SLIPPAGE_RATE, ge=0, le=1)
    sell_tax_rate: Decimal = Field(default=DEFAULT_SELL_TAX_RATE, ge=0, le=1)
    quantity: Decimal = Field(default=DEFAULT_QUANTITY, gt=0)
    initial_capital: Decimal = Field(default=DEFAULT_INITIAL_CAPITAL, gt=0)

    @field_validator("start", "end")
    @classmethod
    def _as_utc(cls, value: datetime) -> datetime:
        """Bar timestamps are always UTC-aware, and comparing one against a
        naive datetime raises rather than mis-sorting -- so a request that
        omitted its offset is read as UTC here, once, instead of blowing up
        deep inside the replay."""
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @model_validator(mode="after")
    def _check_one_source_and_a_forward_range(self) -> "BacktestRunRequest":
        if (self.strategy_id is None) == (self.source_code is None):
            raise ValueError(
                "請擇一提供 strategy_id（回測已存檔的策略）或 source_code（回測草稿），"
                "不能同時給，也不能兩個都不給。"
            )
        if self.end <= self.start:
            raise ValueError("回測的結束時間必須晚於開始時間。")
        return self

    def to_assumptions(self) -> BacktestAssumptions:
        return BacktestAssumptions(
            fill_price_basis=self.fill_price_basis,
            commission_rate=self.commission_rate,
            slippage_rate=self.slippage_rate,
            sell_tax_rate=self.sell_tax_rate,
            quantity=self.quantity,
            initial_capital=self.initial_capital,
        )


class BacktestTradeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    opened_at: datetime
    closed_at: datetime
    quantity: MoneyStr
    entry_price: MoneyStr
    exit_price: MoneyStr
    pnl: MoneyStr
    return_pct: MoneyStr


class EquityPointRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    timestamp: datetime
    close: MoneyStr
    position_qty: MoneyStr
    cash: MoneyStr
    equity: MoneyStr


class BacktestSummaryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    bars_total: int
    bars_tested: int
    signals: int
    skipped_signals: int
    unfilled_signals: int
    trade_count: int
    wins: int
    losses: int
    win_rate_pct: MoneyStr | None
    average_win: MoneyStr | None
    average_loss: MoneyStr | None
    net_pnl: MoneyStr
    total_costs: MoneyStr
    total_return_pct: MoneyStr
    max_drawdown_pct: MoneyStr
    final_equity: MoneyStr
    open_quantity: MoneyStr
    open_avg_entry_price: MoneyStr


class BacktestResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    strategy_name: str
    symbol: str
    timeframe: str
    entry_point: str
    warmup_bars: int
    first_bar_at: datetime | None
    last_bar_at: datetime | None
    assumptions: BacktestAssumptionsRead
    assumption_notes: list[str]
    notes: list[str]
    trades: list[BacktestTradeRead]
    equity_curve: list[EquityPointRead]
    summary: BacktestSummaryRead


class BacktestRunRead(BaseModel):
    """One row of the history list. Deliberately no equity curve: the list is
    for scanning, and shipping every run's full chart on every poll is how a
    free-tier box runs out of bandwidth."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    strategy_id: int | None
    strategy_name: str
    symbol: str
    timeframe: str
    data_source: DataSource
    range_start: datetime
    range_end: datetime
    created_at: datetime
    assumptions: BacktestAssumptionsRead
    summary: BacktestSummaryRead


class BacktestRunDetail(BacktestRunRead):
    """One run in full, including the source it actually scored."""

    source_code: str
    code_hash: str
    result: BacktestResultRead
