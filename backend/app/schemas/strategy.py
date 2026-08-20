from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import DataSource
from app.schemas.common import MoneyStr, UtcDatetime
from app.services import symbol_search


class StrategyRiskOverrides(BaseModel):
    """The eight global risk knobs a strategy may take over for itself.

    None means inherit, and is what a strategy that never opts in keeps
    holding -- see services/risk_resolver.py, which owns that rule. Mixed into
    both write schemas so opting in and opting back out are the same field
    either way: send a number to override, send null to go back to global.
    """

    capital: Decimal | None = Field(default=None, ge=0)
    stop_loss_pct: Decimal | None = Field(default=None, ge=0)
    take_profit_pct: Decimal | None = Field(default=None, ge=0)
    max_position_qty: Decimal | None = Field(default=None, ge=0)
    max_order_notional: Decimal | None = Field(default=None, ge=0)
    max_pending_orders_per_symbol: int | None = Field(default=None, ge=0)
    signal_cooldown_sec: int | None = Field(default=None, ge=0)
    alert_interval_sec: int | None = Field(default=None, ge=0)


def _clean_symbol(value: str | None) -> str | None:
    """Shared by create and update so the two cannot drift apart.

    There was no normaliser at all here: whitespace and case were stored
    verbatim, so 「 aapl 」 and 「AAPL」 became two different strategies polling
    two different (one non-existent) symbols. And 「台積電」 was accepted, which
    produced a strategy that ran forever and never saw a single price.
    """
    if value is None:
        return None
    cleaned = symbol_search.normalise(value)
    if not cleaned:
        raise ValueError("請輸入股票代號。")
    problem = symbol_search.looks_unpriceable(cleaned)
    if problem:
        raise ValueError(problem)
    return cleaned


class StrategyCreate(StrategyRiskOverrides):
    name: str = Field(min_length=1, max_length=120)
    symbol: str = Field(min_length=1, max_length=32)
    data_source: DataSource = DataSource.YFINANCE
    source_code: str = Field(min_length=1)
    default_quantity: Decimal = Decimal(1)
    warmup_bars: int = Field(default=30, ge=0)
    alert_only: bool = False

    @field_validator("symbol")
    @classmethod
    def _check_symbol(cls, value: str) -> str:
        return _clean_symbol(value) or value


class StrategyUpdate(StrategyRiskOverrides):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    symbol: str | None = Field(default=None, min_length=1, max_length=32)
    data_source: DataSource | None = None
    source_code: str | None = Field(default=None, min_length=1)
    default_quantity: Decimal | None = None
    warmup_bars: int | None = Field(default=None, ge=0)
    alert_only: bool | None = None

    @field_validator("symbol")
    @classmethod
    def _check_symbol(cls, value: str | None) -> str | None:
        return _clean_symbol(value)


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
    last_signal_at: UtcDatetime | None
    last_run_at: UtcDatetime | None
    last_blocked_reason: str | None
    last_blocked_at: UtcDatetime | None
    last_error: str | None
    consecutive_errors: int

    # Deliberately not the StrategyRiskOverrides mixin: these are read back as
    # MoneyStr so a Numeric column round-tripped through SQLite doesn't reach
    # the UI as "0E-8".
    capital: MoneyStr | None
    stop_loss_pct: MoneyStr | None
    take_profit_pct: MoneyStr | None
    max_position_qty: MoneyStr | None
    max_order_notional: MoneyStr | None
    max_pending_orders_per_symbol: int | None
    signal_cooldown_sec: int | None
    alert_interval_sec: int | None


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
    # A previous round can come back as a question instead of code. These
    # carry it and the owner's answer into the retry, because ask() is
    # single-turn: anything not restated here never reaches the model.
    question: str | None = Field(default=None, max_length=2000)
    answer: str | None = Field(default=None, max_length=2000)


class StrategyGenerateResult(StrategyValidateResult):
    """Everything /validate returns plus the code it describes, so one round
    trip is enough for the editor to fill in the source and show
    "偵測到：名稱（代號）". source_code is None only when generation never got
    as far as producing any."""

    source_code: str | None = None
    # Set when the model asked for a clarification instead of writing code.
    # A separate field from `error` on purpose: being asked a question is not
    # a failure, and showing it as one would train the owner to ignore it.
    # The alternative -- letting the model guess -- produces a strategy that
    # looks finished and quietly does something else, which the owner cannot
    # read Python well enough to catch.
    question: str | None = None


class SampleStrategyInfo(BaseModel):
    filename: str
    source_code: str


class StrategyPerformanceRead(BaseModel):
    """A live scorecard, on a different basis from the backtest's -- which is
    exactly why `notes` travels with it."""

    model_config = ConfigDict(from_attributes=True)

    total_orders: int
    filled_orders: int
    realized_pnl: MoneyStr | None
    open_quantity: MoneyStr
    open_cost: MoneyStr
    bought_value: MoneyStr
    sold_value: MoneyStr
    notes: list[str]
