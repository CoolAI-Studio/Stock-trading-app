from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import DataSource
from app.schemas.common import MoneyStr, UtcDatetime
from app.services.backtest import (
    DEFAULT_COMMISSION_RATE,
    DEFAULT_EQUITY_PCT,
    DEFAULT_INITIAL_CAPITAL,
    DEFAULT_MINIMUM_FEE,
    DEFAULT_QUANTITY,
    DEFAULT_SELL_TAX_RATE,
    DEFAULT_SLIPPAGE_RATE,
    BacktestAssumptions,
    ExitReason,
    FillPriceBasis,
    PositionSizing,
)
from app.services.market_data.base import Timeframe


class BacktestAssumptionsRead(BaseModel):
    """What the simulation charged for. Echoed back on every response because
    a return figure without its costs is not a number the owner can act on."""

    model_config = ConfigDict(from_attributes=True)

    fill_price_basis: FillPriceBasis
    commission_rate: MoneyStr
    minimum_fee: MoneyStr
    slippage_rate: MoneyStr
    sell_tax_rate: MoneyStr
    # Which of the two size fields below actually decided anything. Echoed
    # like every other assumption: a return sized at a fixed unit count and one
    # sized at a fraction of equity answer different questions, and two runs
    # that do not say which cannot be compared with each other.
    position_sizing: PositionSizing
    quantity: MoneyStr
    equity_pct: MoneyStr
    initial_capital: MoneyStr
    # Echoed like every other assumption: a run that applied a 5% stop and one
    # that applied none are different experiments, and telling them apart
    # afterwards is only possible if the run says which it was.
    stop_loss_pct: MoneyStr
    take_profit_pct: MoneyStr


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
    # A per-leg floor on the commission, in the account currency. Taiwan
    # brokers charge 1 to 20 元 whatever the percentage works out to, which on
    # a small lot is the whole cost. No upper bound: it is an amount, not a
    # rate.
    minimum_fee: Decimal = Field(default=DEFAULT_MINIMUM_FEE, ge=0)
    slippage_rate: Decimal = Field(default=DEFAULT_SLIPPAGE_RATE, ge=0, le=1)
    sell_tax_rate: Decimal = Field(default=DEFAULT_SELL_TAX_RATE, ge=0, le=1)
    # Defaults to the fixed-count sizing every run used before this existed:
    # saved runs are compared with each other, and a changed default would make
    # every historical run incomparable with every new one.
    position_sizing: PositionSizing = PositionSizing.FIXED_QUANTITY
    quantity: Decimal = Field(default=DEFAULT_QUANTITY, gt=0)
    # A fraction of the account, read only under PERCENT_OF_EQUITY. Capped at
    # 1: above that is leverage, and nothing here models a margin account, an
    # interest cost or a margin call.
    equity_pct: Decimal = Field(default=DEFAULT_EQUITY_PCT, gt=0, le=1)
    initial_capital: Decimal = Field(default=DEFAULT_INITIAL_CAPITAL, gt=0)

    # None, not 0, and the difference carries meaning -- the same three-state
    # rule risk_resolver enforces. None means "whatever this strategy would
    # actually run under", which the router resolves; 0 means the owner
    # deliberately asked for a run with no stop at all. Collapsing them would
    # make it impossible to ask "how would this have done WITHOUT my stop",
    # which is one of the more useful questions a backtest can answer.
    stop_loss_pct: Decimal | None = Field(default=None, ge=0, le=1)
    take_profit_pct: Decimal | None = Field(default=None, ge=0)

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

    def to_assumptions(
        self, *, stop_loss_pct: Decimal, take_profit_pct: Decimal
    ) -> BacktestAssumptions:
        """The thresholds are passed in rather than read off `self` because
        only the router can answer what the strategy would actually run
        under -- that needs the database. An explicit request value still
        wins; see the router."""
        return BacktestAssumptions(
            fill_price_basis=self.fill_price_basis,
            commission_rate=self.commission_rate,
            minimum_fee=self.minimum_fee,
            slippage_rate=self.slippage_rate,
            sell_tax_rate=self.sell_tax_rate,
            position_sizing=self.position_sizing,
            quantity=self.quantity,
            equity_pct=self.equity_pct,
            initial_capital=self.initial_capital,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
        )


class BacktestTradeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    opened_at: UtcDatetime
    closed_at: UtcDatetime
    quantity: MoneyStr
    entry_price: MoneyStr
    exit_price: MoneyStr
    pnl: MoneyStr
    return_pct: MoneyStr
    exit_reason: ExitReason


class EquityPointRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    timestamp: UtcDatetime
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
    stop_loss_exits: int
    take_profit_exits: int
    ambiguous_exit_bars: int
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
    buy_and_hold_return_pct: MoneyStr | None
    excess_return_pct: MoneyStr | None
    profit_factor: MoneyStr | None
    exposure_pct: MoneyStr | None


class BacktestResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    strategy_name: str
    symbol: str
    timeframe: str
    entry_point: str
    warmup_bars: int
    first_bar_at: UtcDatetime | None
    last_bar_at: UtcDatetime | None
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
    # The source's fingerprint, but not the source. It is what lets two runs
    # be told apart as "same code, different costs" from "different code" --
    # which is the difference between a comparison and a coincidence -- without
    # shipping every run's full program in a list the page polls.
    code_hash: str
    symbol: str
    timeframe: str
    data_source: DataSource
    range_start: UtcDatetime
    range_end: UtcDatetime
    created_at: UtcDatetime
    assumptions: BacktestAssumptionsRead
    summary: BacktestSummaryRead


class BacktestRunDetail(BacktestRunRead):
    """One run in full, including the source it actually scored."""

    source_code: str
    code_hash: str
    result: BacktestResultRead
