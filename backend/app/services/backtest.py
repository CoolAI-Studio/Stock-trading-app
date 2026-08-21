"""Replaying a strategy over historical candles, so a strategy can be judged
in minutes instead of a fortnight of alert-only watching.

THE ONE RULE THIS MODULE IS BUILT AROUND: a backtest drives the strategy
through the *same* runtime the live loop uses. compile_strategy() gives the
same sandbox and the same `indicators` namespace; LoadedStrategy.on_bar /
on_tick is the same dispatch, under the same per-call timeout; warm-up is
effective_warmup() plus LoadedStrategy.warm_up(), the same pair market_loop
calls. Nothing here re-derives any of that. A backtest of a parallel
implementation would describe code the owner does not actually run, which is
worse than having no backtest at all -- it manufactures confidence in the
wrong artefact.

What IS simulated here, and therefore stated in every result, is the part the
live system leaves to a human and a broker: what price a signal would have
been filled at, and what that fill costs. See BacktestAssumptions.

RISK GATES -- A DELIBERATE CHOICE: the live order-admission gates
(services/risk.py's position limit and capital limit, plus the dedupe,
cooldown and pending-order caps in services/signals.py) do NOT run during a
backtest.

The reason is that every one of those gates is evaluated against state that
does not exist in a replay of 2021: the owner's *current* positions table,
their orders still pending right now, and the wall clock the cooldown measures
against. Feeding them a hypothetical ledger would blend two different
questions into one number, so that "this strategy loses money" and "my capital
cap was set too low last Tuesday" become indistinguishable. The sizing rule
this module does impose -- one fixed-size position at a time -- is stated in
the assumptions rather than inherited from the risk settings, so it cannot
drift when the owner edits those settings.

The defensible opposite (run the gates, and report the strategy as the owner's
whole configured system would have traded it) was rejected for that blending,
not because it is wrong.

STOP-LOSS AND TAKE-PROFIT ARE THE EXCEPTION, and the exception is principled
rather than convenient. market_loop._check_position_exit reads exactly two
things: the entry price of the position it is watching, and the price now.
Both exist inside a replay -- unlike a positions table or a wall clock -- so
omitting them scored a system the owner does not run, which is the very
failure this module's first paragraph exists to prevent. They are off by
default (0, the same "switched off" convention services/risk.py uses) and the
router fills them from the strategy's own resolved risk settings, so a run
describes the strategy as configured.

What a candle cannot supply is the PATH within it. When one candle's low
crosses the stop and its high crosses the target, nothing in the data says
which came first; the stop is assumed to have fired, because that is the only
choice that cannot flatter the result, and every candle it was applied to is
counted into summary.ambiguous_exit_bars and reported.
"""

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from enum import StrEnum

from app.models.enums import DataSource
from app.models.strategy import DEFAULT_WARMUP_BARS
from app.services.market_data.base import Bar, Timeframe, bar_end
from app.services.market_data.service import MarketDataService
from app.services.strategy_runtime import LoadedStrategy, compile_strategy, effective_warmup


class BacktestError(Exception):
    """The strategy blew up part-way through the replay.

    Distinct from StrategyValidationError, which means the source never
    compiled at all: this one carries the candle the run died on, because
    "works on the first 400 bars, divides by zero on the 401st" is the failure
    a backtest is uniquely placed to find.
    """


class FillPriceBasis(StrEnum):
    """Which price a signal is assumed to have been filled at."""

    # The honest default. The strategy decides on a candle's close, so the
    # earliest the owner could actually place that order is after the close --
    # and it executes at the next candle's open.
    NEXT_OPEN = "next_open"
    # Fill at the close the signal was computed from. Flattering and useful
    # for comparing against other tools that do the same, but not a price the
    # owner could have traded at, since they only learn the close once the
    # candle is over.
    CLOSE = "close"


class PositionSizing(StrEnum):
    """How many units each entry buys."""

    # What every run did before this existed, and still the default: a fixed
    # unit count, unrelated to the account. Kept as the default because saved
    # runs are compared with each other, and changing what the default means
    # would make every historical run incomparable with every new one.
    FIXED_QUANTITY = "fixed_quantity"
    # A fraction of what the account is worth at the moment of entry. This is
    # the sizing that makes the headline return mean 「what this strategy did
    # to my money」: under fixed sizing, one unit of a NT$2,375 stock against
    # NT$100,000 is 2.4% invested, so a strategy that DOUBLES that stock
    # reports 2.4% -- and the same strategy on a NT$20 stock reports 0.02%.
    # The two runs are not comparable with each other, which is the entire
    # reason somebody ran both.
    PERCENT_OF_EQUITY = "percent_of_equity"


class ExitReason(StrEnum):
    """Why a round trip ended.

    Recorded per trade because "my rules make money but the stop keeps cutting
    them before they get there" is the single most actionable thing a backtest
    can tell someone, and it is invisible when every exit is just 'sold'.
    """

    # The strategy's own SELL.
    SIGNAL = "signal"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"


# 0.1425% per side is the standard Taiwan brokerage commission, which is the
# market the owner trades. It is the default rather than zero because a
# backtest quoting costless fills as if they were real is a lie of omission --
# on a strategy that trades often, commission alone decides the sign of the
# result.
DEFAULT_COMMISSION_RATE = Decimal("0.001425")
# 5 basis points. A small, deliberately non-zero stand-in for the gap between
# the price on the screen and the price you get.
DEFAULT_SLIPPAGE_RATE = Decimal("0.0005")
# Zero, and said out loud in the assumptions: Taiwan charges 0.3% securities
# transaction tax on every sale, but this app also backtests crypto, where
# that number would be pure invention. The owner sets it per run.
DEFAULT_SELL_TAX_RATE = Decimal(0)

# Zero, so the default stays exactly what it has always been. Real Taiwan
# brokers charge 1 to 20 元 a trade whatever the percentage works out to, which
# on a small lot is the difference between 0.71 元 and 20 元 -- see
# services/broker_costs.py for the per-broker figures.
DEFAULT_MINIMUM_FEE = Decimal(0)
DEFAULT_QUANTITY = Decimal(1)
DEFAULT_INITIAL_CAPITAL = Decimal(100_000)

# All of it. With one position at a time and no leverage, a full-equity entry
# makes the equity curve exactly the strategy's own return, which is the
# question a backtest is being asked. Only read when position_sizing is
# PERCENT_OF_EQUITY.
DEFAULT_EQUITY_PCT = Decimal(1)

# Units are rounded DOWN to this many places when sizing from equity. Eight is
# what a crypto pair needs (a satoshi is 1e-8 BTC) and is harmless for equities.
# Rounding down rather than to nearest, so a sized entry can never cost more
# than the fraction of equity it was allowed.
_SIZE_PLACES = Decimal("0.00000001")

# Zero means "not simulated", which is the same convention services/risk.py's
# check_stop_loss and check_take_profit already use for the live thresholds --
# they have to agree, or the number the owner types into one box would mean
# opposite things in the two places it is read. The API layer fills these from
# the strategy's resolved risk settings, so the default here only decides what
# a direct call to run_backtest() does.
DEFAULT_STOP_LOSS_PCT = Decimal(0)
DEFAULT_TAKE_PROFIT_PCT = Decimal(0)

# One backtest may TEST at most this many candles. The equity curve, the trade
# list and the persisted row all grow with it, and this shares a free-tier box
# (and its process) with the live market loop. 5000 candles is ~20 years of
# daily bars, or ~3.5 days of 1-minute bars -- which is the shape of the guard
# the owner will actually meet: a long range on a small candle.
MAX_BACKTEST_BARS = 5000

# How far back the fetch may reach, which is a different question: a 200-candle
# range that ended in 2021 still needs every candle from then until now,
# because providers serve the NEWEST n rows. Providers cap their own history
# (see yfinance_provider._PERIOD_FOR) long before this ceiling bites.
MAX_HISTORY_FETCH_BARS = 20_000

# Any date will do -- it exists only to ask bar_end() how long one candle of a
# given timeframe lasts, so this module keeps no duration table of its own to
# drift out of step with market_data.base.
_DURATION_ANCHOR = datetime(2000, 1, 1, tzinfo=UTC)

# Numeric(18, 8) is the precision the real ledger stores money at; matching it
# keeps a simulated fill from being a rounder number than a real one.
_MONEY_PLACES = Decimal("0.00000001")
_PCT_PLACES = Decimal("0.0001")


def _money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_PLACES, rounding=ROUND_HALF_UP)


def _pct(value: Decimal) -> Decimal:
    return value.quantize(_PCT_PLACES, rounding=ROUND_HALF_UP)


def _rate_text(rate: Decimal) -> str:
    """A rate as a percentage a human reads, with no trailing zeros and no
    scientific notation -- Decimal('0E-8') must never reach the owner."""
    scaled = rate * 100
    return f"{format(scaled.normalize(), 'f') if scaled else '0'}%"


@dataclass(frozen=True)
class BacktestAssumptions:
    """Everything the simulation had to invent, in one place the owner can
    both set and read back.

    Costs are folded into the fill price rather than tracked as a separate
    ledger line: it keeps the P&L arithmetic identical to the live ledger's
    (services/portfolio.py realizes `(fill_price - entry_price) * quantity`),
    so a trade's reported profit is already net of what it cost to get in and
    out. `total_costs` in the summary reports the difference back out for
    anyone who wants to see it.
    """

    fill_price_basis: FillPriceBasis = FillPriceBasis.NEXT_OPEN
    commission_rate: Decimal = DEFAULT_COMMISSION_RATE
    # A floor on the commission, charged per leg. Cannot be folded into the
    # fill price like the rates above -- it does not scale with the trade, so
    # it comes out of cash separately.
    minimum_fee: Decimal = DEFAULT_MINIMUM_FEE
    slippage_rate: Decimal = DEFAULT_SLIPPAGE_RATE
    sell_tax_rate: Decimal = DEFAULT_SELL_TAX_RATE
    # How the size of each entry is decided. See PositionSizing; the default
    # keeps every existing run byte-identical.
    position_sizing: PositionSizing = PositionSizing.FIXED_QUANTITY
    # Read only under FIXED_QUANTITY.
    quantity: Decimal = DEFAULT_QUANTITY
    # Read only under PERCENT_OF_EQUITY.
    equity_pct: Decimal = DEFAULT_EQUITY_PCT
    initial_capital: Decimal = DEFAULT_INITIAL_CAPITAL
    # Measured from the position's own fill price, the way
    # market_loop._check_position_exit measures from position.avg_entry_price.
    # 0 = not simulated.
    stop_loss_pct: Decimal = DEFAULT_STOP_LOSS_PCT
    take_profit_pct: Decimal = DEFAULT_TAKE_PROFIT_PCT

    def __post_init__(self) -> None:
        # Stated as a contract rather than defended at every use site: a zero
        # capital would make the return and drawdown percentages a division by
        # zero, and a zero size would make every trade a no-op that still
        # looked like a trade.
        if self.initial_capital <= 0:
            raise ValueError("起始本金必須大於 0。")
        # Each mode validates only the field it reads. Demanding a meaningful
        # quantity from a percent run -- or a meaningful percentage from a
        # fixed one -- is how a form grows a question nobody can answer.
        if self.position_sizing == PositionSizing.FIXED_QUANTITY:
            if self.quantity <= 0:
                raise ValueError("每次下單數量必須大於 0。")
        elif not (0 < self.equity_pct <= 1):
            # Above 1 would be leverage, and nothing here models a margin
            # account, an interest cost or a margin call. Reporting a levered
            # return from a simulation that cannot be liquidated is the most
            # flattering wrong answer this module could give.
            raise ValueError("每次投入的本金比例必須大於 0 且不超過 1（100%）。")
        for name, rate in (
            ("手續費率", self.commission_rate),
            ("滑價率", self.slippage_rate),
            ("交易稅率", self.sell_tax_rate),
            # Negative rather than zero is the dangerous one: read literally, a
            # negative stop-loss puts the threshold ABOVE the entry price, so
            # every position is "already stopped out" on the candle it opened.
            ("停損比例", self.stop_loss_pct),
            ("停利比例", self.take_profit_pct),
        ):
            if rate < 0:
                raise ValueError(f"{name}不可以是負數。")


@dataclass(frozen=True)
class BacktestTrade:
    """One realized round trip: bought, then sold. Prices are the simulated
    fills, so they already carry slippage, commission and tax."""

    opened_at: datetime
    closed_at: datetime
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    pnl: Decimal
    return_pct: Decimal
    exit_reason: ExitReason = ExitReason.SIGNAL


@dataclass(frozen=True)
class EquityPoint:
    """One point of the chart: the account marked to that candle's close."""

    timestamp: datetime
    close: Decimal
    position_qty: Decimal
    cash: Decimal
    equity: Decimal


@dataclass(frozen=True)
class BacktestSummary:
    bars_total: int
    bars_tested: int
    signals: int
    skipped_signals: int
    unfilled_signals: int
    trade_count: int
    wins: int
    losses: int
    # How many round trips ended because a threshold was crossed rather than
    # because the strategy said so. Separated because they answer different
    # questions: a strategy whose every exit is the stop has not really been
    # tested, it has been managed.
    stop_loss_exits: int
    take_profit_exits: int
    # Candles that crossed BOTH thresholds, where which one came first is not
    # in the data. Reported because a result resting on twenty of these is a
    # guess wearing a percentage sign.
    ambiguous_exit_bars: int
    # None rather than 0 when there were no trades: "0% win rate" reads as a
    # strategy that lost every time, not as one that never traded.
    win_rate_pct: Decimal | None
    average_win: Decimal | None
    average_loss: Decimal | None
    net_pnl: Decimal
    total_costs: Decimal
    total_return_pct: Decimal
    max_drawdown_pct: Decimal
    final_equity: Decimal
    open_quantity: Decimal
    open_avg_entry_price: Decimal

    # What doing nothing would have returned over the same bars. Without it
    # +18% reads as a good year, when the stock itself may have done +40% and
    # the strategy destroyed value. In a bull run almost anything is
    # profitable; this is the only line that separates "the strategy works"
    # from "the market went up". None when there were no bars to hold.
    buy_and_hold_return_pct: Decimal | None
    # The strategy's return minus that. Negative means holding would have been
    # better, which is the number worth acting on.
    excess_return_pct: Decimal | None
    # Gross profit divided by gross loss. Answers what win rate cannot: a 75%
    # win rate that makes 1 and loses 5 is a losing strategy. None when
    # nothing lost -- reporting infinity would read as brilliance rather than
    # as too few trades to judge.
    profit_factor: Decimal | None
    # How much of the tested period the money was actually at risk. A 10%
    # return earned while invested a tenth of the time is a different
    # proposition from one fully invested throughout.
    exposure_pct: Decimal | None


@dataclass(frozen=True)
class BacktestResult:
    strategy_name: str
    symbol: str
    timeframe: str
    entry_point: str
    warmup_bars: int
    first_bar_at: datetime | None
    last_bar_at: datetime | None
    assumptions: BacktestAssumptions
    # What the simulation assumed, in Traditional Chinese. Travels with the
    # numbers so a result can never be read without them.
    assumption_notes: list[str]
    # What actually happened in this particular run that the owner should know
    # about: signals that could not be acted on, a position still open at the
    # end, a range too short to test.
    notes: list[str]
    trades: list[BacktestTrade]
    equity_curve: list[EquityPoint]
    summary: BacktestSummary


# --- how long a candle lasts, and how many fit in a range -------------------


def estimated_bar_count(start: datetime, end: datetime, timeframe: Timeframe) -> int:
    """Upper bound on the candles in [start, end], before anything is fetched.

    An upper bound is the useful direction: it counts calendar time, so a year
    of daily candles comes out as 365 rather than the ~250 sessions that
    actually trade. That makes it a guard that never lets through more work
    than it promised.
    """
    if end <= start:
        return 0
    step = bar_end(_DURATION_ANCHOR, timeframe) - _DURATION_ANCHOR
    return math.ceil((end - start) / step)


def load_backtest_bars(
    service: MarketDataService,
    *,
    symbol: str,
    timeframe: Timeframe,
    data_source: DataSource,
    start: datetime,
    end: datetime,
    now: datetime | None = None,
) -> list[Bar]:
    """The closed candles of [start, end], newest last.

    Goes through MarketDataService rather than a provider directly, so a
    backtest gets the same forming-candle removal (closed_bars) and the same
    rate-limit cache the live loop relies on -- an owner re-running a backtest
    five times must not be five more requests at Yahoo.

    A candle belongs to the range if the instant it OPENED falls inside it,
    which is the convention every chart the owner has ever looked at uses --
    ask for a range ending on the 15th and you expect the 15th's candle. Said
    out loud because it has one consequence: a weekly candle that opens on the
    last day of the range carries six days of price action from after it. That
    is harmless here (nothing is being validated out-of-sample) but it would
    not be if this range were ever used to split in-sample from out-of-sample
    data.
    """
    now = now or datetime.now(UTC)
    # Providers serve the newest `limit` rows, so reaching a range that ended
    # months ago means asking for everything since that range STARTED, not
    # just the range's own length.
    depth = min(
        max(estimated_bar_count(start, max(end, now), timeframe), 1), MAX_HISTORY_FETCH_BARS
    )
    bars = service.get_bars(symbol, timeframe, data_source, limit=depth)
    return [bar for bar in bars if start <= bar.timestamp <= end]


# --- the simulated account --------------------------------------------------


@dataclass
class _Account:
    """One fixed-size position at a time, plus cash. Mutable and private: it
    is the loop's scratch space, never part of the result."""

    cash: Decimal
    quantity: Decimal = Decimal(0)
    entry_price: Decimal = Decimal(0)
    opened_at: datetime | None = None
    costs: Decimal = Decimal(0)
    trades: list[BacktestTrade] = field(default_factory=list)


def _fill_price(reference: Decimal, side: str, assumptions: BacktestAssumptions) -> Decimal:
    """The reference price, worsened by every cost, in the direction that
    always hurts: a buy fills higher, a sale fills lower."""
    if side == "BUY":
        worsened = (
            reference
            * (Decimal(1) + assumptions.slippage_rate)
            * (Decimal(1) + assumptions.commission_rate)
        )
    else:
        worsened = (
            reference
            * (Decimal(1) - assumptions.slippage_rate)
            * (Decimal(1) - assumptions.commission_rate - assumptions.sell_tax_rate)
        )
    return _money(worsened)


def _minimum_fee_shortfall(
    reference: Decimal, quantity: Decimal, assumptions: BacktestAssumptions
) -> Decimal:
    """How much more than the percentage the broker would actually charge.

    Zero when there is no floor, or when the trade is big enough to clear it,
    which keeps every existing result byte-identical.
    """
    if assumptions.minimum_fee <= 0:
        return Decimal(0)
    proportional = reference * quantity * assumptions.commission_rate
    return max(Decimal(0), assumptions.minimum_fee - proportional)


def _exit_trigger(
    entry_price: Decimal, bar: Bar, assumptions: BacktestAssumptions
) -> tuple[Decimal, ExitReason, bool] | None:
    """Where a held position would have been forced out on this candle.

    Returns the price the exit is assumed to have happened at, why, and
    whether the candle also crossed the other threshold -- i.e. whether the
    answer had to be guessed. None means the candle never reached either.

    THE FILL PRICE IS NOT THE HIGH OR THE LOW. The threshold is where the
    order fires, so the threshold is what it fires at; using the candle's
    extreme would report an exit at the best (or worst) price of the day,
    which nobody gets. The one exception is a gap: if the candle OPENED past
    the threshold, no trade ever happened at the threshold, so the fill is the
    open. That cuts both ways and is left to cut both ways -- a gap down fills
    below the stop (worse), a gap up fills above the target (better) -- because
    that is what actually happens, and shading either one would be a thumb on
    the scale.
    """
    if entry_price <= 0:
        return None

    stop: Decimal | None = None
    if assumptions.stop_loss_pct > 0:
        threshold = entry_price * (Decimal(1) - assumptions.stop_loss_pct)
        if Decimal(str(bar.low)) <= threshold:
            stop = min(Decimal(str(bar.open)), threshold)

    target: Decimal | None = None
    if assumptions.take_profit_pct > 0:
        threshold = entry_price * (Decimal(1) + assumptions.take_profit_pct)
        if Decimal(str(bar.high)) >= threshold:
            target = max(Decimal(str(bar.open)), threshold)

    if stop is not None:
        # Stop first when both were touched: a candle records four prices, not
        # a path, and this is the only reading that cannot make the result
        # look better than the evidence supports.
        return stop, ExitReason.STOP_LOSS, target is not None
    if target is not None:
        return target, ExitReason.TAKE_PROFIT, False
    return None


def _entry_quantity(account: _Account, price: Decimal, assumptions: BacktestAssumptions) -> Decimal:
    """How many units this entry buys. Zero means it cannot happen.

    Under PERCENT_OF_EQUITY the account is always flat when this is reached
    (the replay refuses a BUY while holding), so equity is simply cash.

    `price` already carries the proportional costs -- commission and slippage
    are folded into the fill -- so it is the all-in cost per unit. The one cost
    it cannot carry is the broker's per-leg minimum, which does not scale with
    the trade; that is reserved off the top so a full-equity entry still has
    the fee left to pay and cash cannot go negative.
    """
    if assumptions.position_sizing is PositionSizing.FIXED_QUANTITY:
        return assumptions.quantity
    if price <= 0:
        # yfinance rounds to four decimals, so a sub-cent penny stock really
        # can arrive as 0.0 -- the same guard the trade's return_pct needs.
        return Decimal(0)
    budget = account.cash * assumptions.equity_pct - assumptions.minimum_fee
    if budget <= 0:
        return Decimal(0)
    # Rounded DOWN, so a sized entry can never cost more than the fraction of
    # equity it was allowed.
    return (budget / price).quantize(_SIZE_PLACES, rounding=ROUND_DOWN)


def _execute(
    account: _Account,
    side: str,
    reference: Decimal,
    at: datetime,
    assumptions: BacktestAssumptions,
    exit_reason: ExitReason = ExitReason.SIGNAL,
) -> bool:
    """Fill one leg. False means nothing happened and the book is unchanged.

    Only a BUY can come back False, and only under equity sizing: an account
    too small to buy anything at this price. Opening it anyway at zero units
    would put a row in the ledger that moved no money -- something that reads
    as a trade and is not one.
    """
    price = _fill_price(reference, side, assumptions)
    # The exit sells what is actually held, which under equity sizing is not
    # `assumptions.quantity` and never was under any sizing that compounds.
    quantity = _entry_quantity(account, price, assumptions) if side == "BUY" else account.quantity
    if quantity <= 0:
        return False
    account.costs += abs(price - reference) * quantity

    # The percentage is already inside `price`; this is only the shortfall up
    # to the broker's floor, which is why it is deducted rather than folded in.
    # Charged on both legs, because both legs pay commission -- a round trip on
    # one small lot in Taiwan really does cost 40 元 before the stock moves.
    shortfall = _minimum_fee_shortfall(reference, quantity, assumptions)
    if shortfall > 0:
        account.cash -= shortfall
        account.costs += shortfall

    if side == "BUY":
        account.cash -= price * quantity
        account.quantity = quantity
        account.entry_price = price
        account.opened_at = at
        return True

    account.cash += price * quantity
    pnl = (price - account.entry_price) * quantity
    account.trades.append(
        BacktestTrade(
            opened_at=account.opened_at or at,
            closed_at=at,
            quantity=quantity,
            entry_price=account.entry_price,
            exit_price=price,
            pnl=_money(pnl),
            # Per unit, so it reads as "this trade made 6%" regardless of size.
            # The zero guard is not theoretical: yfinance rounds prices to four
            # decimals, so a sub-cent penny stock really can arrive as 0.0.
            return_pct=(
                _pct((price - account.entry_price) / account.entry_price * 100)
                if account.entry_price
                else Decimal(0)
            ),
            exit_reason=exit_reason,
        )
    )
    account.quantity = Decimal(0)
    account.entry_price = Decimal(0)
    account.opened_at = None
    return True


# --- the replay -------------------------------------------------------------


def _dispatch(loaded: LoadedStrategy, bar: Bar) -> str:
    """The same two entry points market_loop dispatches to, chosen the same
    way. An on_tick strategy gets the candle's close as its 'quote' -- see the
    assumption note, which says so to the owner."""
    if loaded.entry_point == "on_bar":
        return loaded.on_bar(bar)
    return loaded.on_tick(bar.close)


def run_backtest(
    *,
    source_code: str,
    bars: list[Bar],
    symbol: str | None = None,
    timeframe: Timeframe | None = None,
    assumptions: BacktestAssumptions | None = None,
    stored_warmup_bars: int = DEFAULT_WARMUP_BARS,
    params: dict | None = None,
) -> BacktestResult:
    """Replay `bars` through `source_code` and score the result.

    Takes the candles rather than fetching them, so the walk-forward loop --
    the part that must never see a future bar -- has no I/O in it and can be
    tested against a handful of hand-written candles. load_backtest_bars() is
    the other half.

    `symbol` and `timeframe` are what the run was ASKED for. They are stated
    rather than read off the candles because a symbol with no history has no
    candles to read them off, and a result labelled with the source's
    `self.symbol` in that case would name a stock the run never looked at.

    Raises StrategyValidationError if the source does not compile in the live
    sandbox, and BacktestError if it compiles but fails mid-replay.
    """
    assumptions = assumptions or BacktestAssumptions()
    # The same parameters the live strategy runs under, or the sweep's
    # candidate set. A backtest of the author's defaults, when the owner has
    # tuned them, scores code nobody is running.
    loaded = compile_strategy(source_code, params=params)

    if loaded.entry_point == "on_bar":
        warmup = effective_warmup(loaded, stored_warmup_bars)
    else:
        # The live loop applies no warm-up to on_tick either: _run_tick_strategy
        # acts on the very first quote it sees, and "not enough data yet" is
        # left to the strategy's own `if len(self.prices) < 5: return "HOLD"`.
        # Imposing one here would make the backtest stricter than reality.
        warmup = 0

    notes: list[str] = []
    # The candles that may produce a signal. Everything before them is warm-up
    # and is replayed with its signals thrown away -- market_loop's rule, and
    # the reason a strategy's first backtested decision lands on the same
    # candle it would have landed on live.
    tested = bars[warmup:]

    if not tested:
        notes.append(_no_data_note(bars, warmup))
    elif warmup:
        try:
            loaded.warm_up(bars[:warmup])
        except Exception as exc:
            raise BacktestError(f"策略在暖身階段就發生錯誤：{exc}") from exc

    account = _Account(cash=assumptions.initial_capital)
    equity_curve: list[EquityPoint] = []
    # The side of an order placed on the previous candle's close and still
    # waiting for the next candle to open. None means nothing is in flight.
    pending_side: str | None = None
    signals = 0
    skipped_buy_while_holding = 0
    # Equity sizing can produce a zero-unit entry when the account is too small
    # to buy anything at this price. Counted rather than silently dropped: a
    # backtest that quietly skipped half its trades reports a return for a
    # strategy that never ran.
    skipped_buy_unaffordable = 0
    skipped_sell_while_flat = 0
    unfilled = 0
    stop_loss_exits = 0
    take_profit_exits = 0
    ambiguous_exit_bars = 0
    peak_equity = assumptions.initial_capital
    max_drawdown = Decimal(0)

    for bar in tested:
        # 1) An order placed on the previous candle's close executes at this
        #    candle's open -- before the strategy is shown this candle, which
        #    is the order the two events really happen in.
        if pending_side is not None:
            if not _execute(
                account, pending_side, Decimal(str(bar.open)), bar.timestamp, assumptions
            ):
                skipped_buy_unaffordable += 1
            pending_side = None

        # 2) The stop-loss / take-profit check, in the same place the live
        #    loop makes it: against an open position, on price alone, with no
        #    reference to what the strategy is about to say. It runs BEFORE
        #    the dispatch below because the strategy decides at the close and
        #    a threshold triggers wherever in the candle the price reached it
        #    -- never later than the close. Reversed, a strategy could sell at
        #    a price it had already been stopped out of.
        if account.quantity > 0:
            triggered = _exit_trigger(account.entry_price, bar, assumptions)
            if triggered is not None:
                reference, reason, was_ambiguous = triggered
                if was_ambiguous:
                    ambiguous_exit_bars += 1
                if reason is ExitReason.STOP_LOSS:
                    stop_loss_exits += 1
                else:
                    take_profit_exits += 1
                _execute(account, "SELL", reference, bar.timestamp, assumptions, reason)

        # 3) The strategy sees exactly one candle: this one. It has never been
        #    handed the series, so there is no future for it to read.
        try:
            signal = _dispatch(loaded, bar)
        except Exception as exc:
            raise BacktestError(
                f"策略在 {bar.timestamp:%Y-%m-%d %H:%M} 這根 K 棒發生錯誤：{exc}"
            ) from exc

        # 4) Act on the signal. Nothing can still be in flight here -- step 1
        #    executes and clears any order from the previous candle before the
        #    strategy is asked for a new one -- so the book is either flat or
        #    holding, never mid-order.
        if signal in ("BUY", "SELL"):
            signals += 1
            if signal == "BUY" and account.quantity > 0:
                skipped_buy_while_holding += 1
            elif signal == "SELL" and account.quantity == 0:
                skipped_sell_while_flat += 1
            elif assumptions.fill_price_basis is FillPriceBasis.CLOSE:
                if not _execute(
                    account, signal, Decimal(str(bar.close)), bar.timestamp, assumptions
                ):
                    skipped_buy_unaffordable += 1
            else:
                pending_side = signal

        # 5) Mark to market on this candle's close, after any fill it carried.
        close = Decimal(str(bar.close))
        equity = account.cash + account.quantity * close
        equity_curve.append(
            EquityPoint(
                timestamp=bar.timestamp,
                close=close,
                position_qty=account.quantity,
                cash=_money(account.cash),
                equity=_money(equity),
            )
        )
        peak_equity = max(peak_equity, equity)
        if peak_equity > 0:
            max_drawdown = max(max_drawdown, (peak_equity - equity) / peak_equity * 100)

    if pending_side is not None:
        unfilled = 1
        notes.append(
            "最後一根 K 棒出現訊號，但後面沒有下一根 K 棒可以成交，因此列為未成交、不計入績效。"
        )

    notes.extend(
        _run_notes(
            account=account,
            tested=tested,
            skipped_buy_while_holding=skipped_buy_while_holding,
            skipped_sell_while_flat=skipped_sell_while_flat,
            skipped_buy_unaffordable=skipped_buy_unaffordable,
            ambiguous_exit_bars=ambiguous_exit_bars,
        )
    )

    summary = _summarize(
        account=account,
        assumptions=assumptions,
        bars_total=len(bars),
        tested=tested,
        signals=signals,
        skipped_signals=(
            skipped_buy_while_holding + skipped_sell_while_flat + skipped_buy_unaffordable
        ),
        unfilled_signals=unfilled,
        stop_loss_exits=stop_loss_exits,
        take_profit_exits=take_profit_exits,
        ambiguous_exit_bars=ambiguous_exit_bars,
        max_drawdown=max_drawdown,
        curve=equity_curve,
    )

    return BacktestResult(
        strategy_name=loaded.name,
        symbol=symbol or (bars[0].symbol if bars else loaded.symbol),
        timeframe=(
            timeframe.value
            if timeframe is not None
            else (bars[0].timeframe.value if bars else loaded.timeframe.value)
        ),
        entry_point=loaded.entry_point,
        warmup_bars=warmup,
        first_bar_at=tested[0].timestamp if tested else None,
        last_bar_at=tested[-1].timestamp if tested else None,
        assumptions=assumptions,
        assumption_notes=_assumption_notes(assumptions, loaded.entry_point, warmup),
        notes=notes,
        trades=account.trades,
        equity_curve=equity_curve,
        summary=summary,
    )


# How far the first bar may fall short of the requested start before it is
# worth saying so. Weekends, holidays and a market's own listing date mean the
# first candle is almost never exactly the date asked for, and warning every
# time would train the owner to ignore the warning.
_TRUNCATION_TOLERANCE = timedelta(days=14)


def truncation_note(
    asked_start: datetime,
    asked_end: datetime,
    first_bar_at: datetime | None,
    last_bar_at: datetime | None,
) -> str | None:
    """Says so when the data did not reach back as far as the owner asked.

    Providers cap history by interval and return what they have without
    comment -- five years for daily bars, 60 days for 5-minute, five for
    1-minute. The result reported the requested range regardless, so a run
    that never saw a bar before 2021 was displayed as covering 2015 onwards,
    and the owner would reasonably believe the strategy had survived a period
    it was never shown.
    """
    if first_bar_at is None or last_bar_at is None:
        # Already covered by a clearer message about there being no data.
        return None
    missing = first_bar_at - asked_start
    if missing <= _TRUNCATION_TOLERANCE:
        return None
    return (
        f"實際測到的區間比你要求的短：你要 {asked_start:%Y/%m/%d} 起，"
        f"但資料來源只給到 {first_bar_at:%Y/%m/%d} 為止（少了約 {missing.days} 天）。"
        "行情供應商會依 K 棒週期限制歷史長度（日線約 5 年、小時線約 2 年、"
        "5 分線約 60 天），下面的績效只涵蓋實際測到的那一段。"
    )


def _no_data_note(bars: list[Bar], warmup: int) -> str:
    """Why a run tested nothing. The two causes need different fixes, so they
    get different sentences rather than one shrug."""
    if not bars:
        return (
            "這個區間沒有取得任何已收盤的 K 棒。可能是代號打錯、資料來源沒有這段歷史，"
            "或區間全都落在未來。"
        )
    return (
        f"只取得 {len(bars)} 根 K 棒，少於暖身需要的 {warmup} 根，所以沒有任何一根進入測試。"
        "請把區間拉長，或在策略裡調低 warmup_bars。"
    )


def _sizing_note(assumptions: BacktestAssumptions) -> str:
    """How big each trade was, which decides what the return figure means.

    Under fixed sizing the return is 「what N units of this stock did」, which is
    not comparable between two stocks at different price levels and never
    compounds. Under equity sizing it is 「what this strategy did to the
    account」. The two answer different questions, so the run has to say which
    one it answered.
    """
    if assumptions.position_sizing is PositionSizing.FIXED_QUANTITY:
        return (
            f"每次下單數量固定 {format(assumptions.quantity.normalize(), 'f')} 單位，"
            "而且一次只持有一個部位，不加碼、不放空。"
        )
    pct = assumptions.equity_pct * 100
    return (
        f"每次進場投入當下帳戶淨值的 {format(pct.normalize(), 'f')}%，"
        "而且一次只持有一個部位，不加碼、不放空。獲利會滾入下一筆的部位大小 —— "
        "報酬率因此代表「這個策略對這個帳戶做了什麼」，可以跟別支策略直接比較。"
        "不模擬整股／零股的股數限制（台股一張是 1000 股），會買到小數單位；"
        "無條件捨去到小數點後 8 位，不會超買。"
    )


def _run_notes(
    *,
    account: _Account,
    tested: list[Bar],
    skipped_buy_while_holding: int,
    skipped_sell_while_flat: int,
    skipped_buy_unaffordable: int = 0,
    ambiguous_exit_bars: int = 0,
) -> list[str]:
    notes: list[str] = []
    if ambiguous_exit_bars:
        notes.append(
            f"有 {ambiguous_exit_bars} 根 K 棒同時碰到停損價和停利價。K 棒只留下開、高、低、"
            "收四個價格，無法確定哪一邊先到，這裡一律當成停損先觸發 —— "
            "那是唯一不會讓結果變好看的選法。這幾筆的結果是估的，不是測出來的；"
            "如果它們佔的比例不低，換成更小的 K 棒週期再測一次會準得多。"
        )
    if skipped_buy_unaffordable:
        notes.append(
            f"有 {skipped_buy_unaffordable} 次買進訊號因為帳戶剩餘資金買不到任何單位而略過。"
            "這代表策略在那段期間等於沒有在跑，上面的報酬率是「剩下那些有成交的交易」"
            "的結果，不是策略本身的結果。"
        )
    if skipped_buy_while_holding:
        notes.append(
            f"有 {skipped_buy_while_holding} 次買進訊號因為已有部位而略過"
            "（本回測一次只持有一個部位，不加碼）。"
        )
    if skipped_sell_while_flat:
        notes.append(
            f"有 {skipped_sell_while_flat} 次賣出訊號因為手上沒有部位而略過"
            "（與實際下單一樣不放空）。"
        )
    if account.quantity > 0 and tested:
        last_close = Decimal(str(tested[-1].close))
        unrealized = _money((last_close - account.entry_price) * account.quantity)
        notes.append(
            f"回測結束時還持有 {format(account.quantity.normalize(), 'f')} 單位，"
            f"已用最後一根 K 棒收盤價 {format(last_close.normalize(), 'f')} 結算，"
            f"未實現損益 {format(unrealized.normalize(), 'f')}。這筆沒有算進勝率。"
        )
    return notes


def _profit_factor(gross_profit: Decimal, gross_loss: Decimal) -> Decimal | None:
    """Gross profit over gross loss, both positive.

    None rather than infinity when nothing lost: a strategy with no losing
    trade has no ratio, and a huge number would be read as a great strategy
    instead of as too small a sample to say anything.
    """
    if gross_loss <= 0:
        return None
    return _pct(gross_profit / gross_loss)


def _buy_and_hold_return(tested: list[Bar]) -> Decimal | None:
    """What holding from the first tested bar to the last would have made.

    Measured over the bars the strategy actually traded, not the range that
    was requested -- comparing against a period the strategy never saw is not
    a comparison.
    """
    if len(tested) < 2:
        return None
    first = Decimal(str(tested[0].close))
    last = Decimal(str(tested[-1].close))
    if first <= 0:
        return None
    return _pct((last - first) / first * 100)


def _exposure(curve: list[EquityPoint]) -> Decimal | None:
    if not curve:
        return None
    held = sum(1 for point in curve if point.position_qty > 0)
    return _pct(Decimal(held) / Decimal(len(curve)) * 100)


def _summarize(
    *,
    account: _Account,
    assumptions: BacktestAssumptions,
    bars_total: int,
    tested: list[Bar],
    signals: int,
    skipped_signals: int,
    unfilled_signals: int,
    stop_loss_exits: int,
    take_profit_exits: int,
    ambiguous_exit_bars: int,
    max_drawdown: Decimal,
    curve: list[EquityPoint],
) -> BacktestSummary:
    wins = [trade for trade in account.trades if trade.pnl > 0]
    losses = [trade for trade in account.trades if trade.pnl < 0]
    trade_count = len(account.trades)

    last_close = Decimal(str(tested[-1].close)) if tested else Decimal(0)
    final_equity = account.cash + account.quantity * last_close
    total_return = _pct(
        (final_equity - assumptions.initial_capital) / assumptions.initial_capital * 100
    )
    buy_and_hold = _buy_and_hold_return(tested)
    gross_profit = sum((t.pnl for t in wins), Decimal(0))
    gross_loss = abs(sum((t.pnl for t in losses), Decimal(0)))

    return BacktestSummary(
        bars_total=bars_total,
        bars_tested=len(tested),
        signals=signals,
        skipped_signals=skipped_signals,
        unfilled_signals=unfilled_signals,
        trade_count=trade_count,
        wins=len(wins),
        losses=len(losses),
        stop_loss_exits=stop_loss_exits,
        take_profit_exits=take_profit_exits,
        ambiguous_exit_bars=ambiguous_exit_bars,
        win_rate_pct=(
            _pct(Decimal(len(wins)) / Decimal(trade_count) * 100) if trade_count else None
        ),
        average_win=(_money(sum((t.pnl for t in wins), Decimal(0)) / len(wins)) if wins else None),
        average_loss=(
            _money(sum((t.pnl for t in losses), Decimal(0)) / len(losses)) if losses else None
        ),
        # Realized only -- an open position's paper profit is reported as a
        # note instead, because counting it would let a strategy that never
        # exits look profitable on an unclosed bet.
        net_pnl=_money(sum((t.pnl for t in account.trades), Decimal(0))),
        total_costs=_money(account.costs),
        # This one DOES include the open position, because it is the answer to
        # "what would my account be worth today", not "what did I bank".
        total_return_pct=total_return,
        max_drawdown_pct=_pct(max_drawdown),
        final_equity=_money(final_equity),
        open_quantity=account.quantity,
        open_avg_entry_price=account.entry_price,
        buy_and_hold_return_pct=buy_and_hold,
        excess_return_pct=(_pct(total_return - buy_and_hold) if buy_and_hold is not None else None),
        profit_factor=_profit_factor(gross_profit, gross_loss),
        exposure_pct=_exposure(curve),
    )


def _exit_note(assumptions: BacktestAssumptions) -> str:
    """Whether the stop and the target were simulated, and on what rules.

    Says so in both directions on purpose. Silence about a switched-off stop
    would be read as "it was applied" by anyone who knows the live loop has
    one -- and that reader would then take a number describing a strategy that
    rides every loss to the bottom as evidence about the strategy they run.
    """
    stop_on = assumptions.stop_loss_pct > 0
    target_on = assumptions.take_profit_pct > 0

    if not stop_on and not target_on:
        return (
            "這次沒有模擬停損／停利（兩個都設成 0）。實際執行時，market_loop 會盯著每一個"
            "持倉的成本價，一碰到就發出賣出提醒；這份回測沒有套用，所以它描述的是"
            "「訊號進、訊號出」的結果，跟你設了停損的實際狀況不一樣。"
        )

    bits = [
        f"停損 {_rate_text(assumptions.stop_loss_pct)}" if stop_on else "停損沒有設",
        f"停利 {_rate_text(assumptions.take_profit_pct)}" if target_on else "停利沒有設",
    ]
    note = (
        "、".join(bits) + "，與實際執行同一套規則：從這個部位自己的成交成本價往外算，"
        "K 棒的最低價／最高價碰到就出場。成交價就用觸發價本身；"
        "但如果 K 棒一開盤就已經跳空穿過去，改用開盤價 —— 跳空的那一段沒有人在那裡成交過，"
        "用觸發價會算出一個當時根本買不到、賣不掉的價格。"
    )
    if stop_on and target_on:
        note += "同一根 K 棒同時碰到兩邊時，一律當成停損先觸發（那一邊比較不好看）。"
    return note


def _assumption_notes(
    assumptions: BacktestAssumptions, entry_point: str, warmup_bars: int
) -> list[str]:
    """The simulation's assumptions, in the owner's language.

    Written out in full every time rather than left to a docs page: the number
    a backtest produces is only meaningful next to what it charged for, and a
    result that travels without its assumptions eventually gets read without
    them.
    """
    if assumptions.fill_price_basis is FillPriceBasis.NEXT_OPEN:
        basis = (
            "成交價基準：訊號出現在某根 K 棒收盤時，用「下一根 K 棒的開盤價」成交。"
            "這是比較誠實的假設 —— 你看到收盤價的當下，那個價格已經過去了。"
        )
    else:
        basis = (
            "成交價基準：用「訊號那根 K 棒自己的收盤價」成交。這比實際樂觀，"
            "因為你要等那根 K 棒收完才知道收盤價，屆時已經買不到那個價位。"
        )

    notes = [
        basis,
        f"手續費：單邊 {_rate_text(assumptions.commission_rate)}，買進與賣出各收一次。",
        f"賣出交易稅：{_rate_text(assumptions.sell_tax_rate)}，只在賣出時收。"
        "台股現股請自行設成 0.003（0.3%）；預設是 0，沒有幫你偷偷加上去。",
        f"滑價：{_rate_text(assumptions.slippage_rate)}，方向一律對你不利（買貴、賣便宜）。",
        _sizing_note(assumptions),
        f"起始本金 {format(assumptions.initial_capital.normalize(), 'f')}，"
        "只用來換算報酬率與最大回撤，不會擋下任何一筆買進。",
        "風控閘門：回測「不」套用實際下單的風控（部位上限、單筆金額上限、本金上限、"
        "訊號冷卻、待確認單數上限）。那些規則都是對照你「現在」的持倉與時鐘判斷的，"
        "套進過去只會讓「策略不賺錢」和「當時上限設太低」混在一起。",
        _exit_note(assumptions),
    ]

    if entry_point == "on_tick":
        notes.append(
            "這是 on_tick 策略：實際執行時每次報價都會呼叫一次，但歷史資料只有 K 棒，"
            "所以回測改用每根 K 棒的收盤價當成一次報價。實際的訊號會比這裡密集。"
        )
    if warmup_bars:
        notes.append(
            f"暖身：區間中最前面 {warmup_bars} 根 K 棒只餵給策略累積狀態，不會產生訊號 "
            "—— 與實際執行的規則相同。"
        )
    return notes
