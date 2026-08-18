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
backtest, and neither do the position-level stop-loss / take-profit checks in
market_loop._check_position_exit.

The reason is that every one of those gates is evaluated against state that
does not exist in a replay of 2021: the owner's *current* positions table,
their orders still pending right now, the wall clock the cooldown measures
against, and -- for stop-loss -- the live quote stream, which history does not
have at candle resolution. Feeding them a hypothetical ledger would blend two
different questions into one number, so that "this strategy loses money" and
"my capital cap was set too low last Tuesday" become indistinguishable. This
module therefore answers exactly one question: what would this strategy's
signals have earned on this symbol over this range, at these costs? The
sizing rule it does impose -- one fixed-size position at a time -- is stated
in the assumptions rather than inherited from the risk settings, so it cannot
drift when the owner edits those settings.

The defensible opposite (run the gates, and report the strategy as the owner's
whole configured system would have traded it) was rejected for that blending,
not because it is wrong.
"""

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
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
DEFAULT_QUANTITY = Decimal(1)
DEFAULT_INITIAL_CAPITAL = Decimal(100_000)

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
    slippage_rate: Decimal = DEFAULT_SLIPPAGE_RATE
    sell_tax_rate: Decimal = DEFAULT_SELL_TAX_RATE
    quantity: Decimal = DEFAULT_QUANTITY
    initial_capital: Decimal = DEFAULT_INITIAL_CAPITAL

    def __post_init__(self) -> None:
        # Stated as a contract rather than defended at every use site: a zero
        # capital would make the return and drawdown percentages a division by
        # zero, and a zero size would make every trade a no-op that still
        # looked like a trade.
        if self.initial_capital <= 0:
            raise ValueError("起始本金必須大於 0。")
        if self.quantity <= 0:
            raise ValueError("每次下單數量必須大於 0。")
        for name, rate in (
            ("手續費率", self.commission_rate),
            ("滑價率", self.slippage_rate),
            ("交易稅率", self.sell_tax_rate),
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


def _execute(
    account: _Account,
    side: str,
    reference: Decimal,
    at: datetime,
    assumptions: BacktestAssumptions,
) -> None:
    quantity = assumptions.quantity
    price = _fill_price(reference, side, assumptions)
    account.costs += abs(price - reference) * quantity

    if side == "BUY":
        account.cash -= price * quantity
        account.quantity = quantity
        account.entry_price = price
        account.opened_at = at
        return

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
        )
    )
    account.quantity = Decimal(0)
    account.entry_price = Decimal(0)
    account.opened_at = None


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
    loaded = compile_strategy(source_code)

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
    skipped_sell_while_flat = 0
    unfilled = 0
    peak_equity = assumptions.initial_capital
    max_drawdown = Decimal(0)

    for bar in tested:
        # 1) An order placed on the previous candle's close executes at this
        #    candle's open -- before the strategy is shown this candle, which
        #    is the order the two events really happen in.
        if pending_side is not None:
            _execute(account, pending_side, Decimal(str(bar.open)), bar.timestamp, assumptions)
            pending_side = None

        # 2) The strategy sees exactly one candle: this one. It has never been
        #    handed the series, so there is no future for it to read.
        try:
            signal = _dispatch(loaded, bar)
        except Exception as exc:
            raise BacktestError(
                f"策略在 {bar.timestamp:%Y-%m-%d %H:%M} 這根 K 棒發生錯誤：{exc}"
            ) from exc

        # 3) Act on the signal. Nothing can still be in flight here -- step 1
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
                _execute(
                    account, signal, Decimal(str(bar.close)), bar.timestamp, assumptions
                )
            else:
                pending_side = signal

        # 4) Mark to market on this candle's close, after any fill it carried.
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
            "最後一根 K 棒出現訊號，但後面沒有下一根 K 棒可以成交，因此列為未成交、"
            "不計入績效。"
        )

    notes.extend(
        _run_notes(
            account=account,
            tested=tested,
            skipped_buy_while_holding=skipped_buy_while_holding,
            skipped_sell_while_flat=skipped_sell_while_flat,
        )
    )

    summary = _summarize(
        account=account,
        assumptions=assumptions,
        bars_total=len(bars),
        tested=tested,
        signals=signals,
        skipped_signals=skipped_buy_while_holding + skipped_sell_while_flat,
        unfilled_signals=unfilled,
        max_drawdown=max_drawdown,
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


def _run_notes(
    *,
    account: _Account,
    tested: list[Bar],
    skipped_buy_while_holding: int,
    skipped_sell_while_flat: int,
) -> list[str]:
    notes: list[str] = []
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


def _summarize(
    *,
    account: _Account,
    assumptions: BacktestAssumptions,
    bars_total: int,
    tested: list[Bar],
    signals: int,
    skipped_signals: int,
    unfilled_signals: int,
    max_drawdown: Decimal,
) -> BacktestSummary:
    wins = [trade for trade in account.trades if trade.pnl > 0]
    losses = [trade for trade in account.trades if trade.pnl < 0]
    trade_count = len(account.trades)

    last_close = Decimal(str(tested[-1].close)) if tested else Decimal(0)
    final_equity = account.cash + account.quantity * last_close

    return BacktestSummary(
        bars_total=bars_total,
        bars_tested=len(tested),
        signals=signals,
        skipped_signals=skipped_signals,
        unfilled_signals=unfilled_signals,
        trade_count=trade_count,
        wins=len(wins),
        losses=len(losses),
        win_rate_pct=(
            _pct(Decimal(len(wins)) / Decimal(trade_count) * 100) if trade_count else None
        ),
        average_win=(
            _money(sum((t.pnl for t in wins), Decimal(0)) / len(wins)) if wins else None
        ),
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
        total_return_pct=_pct(
            (final_equity - assumptions.initial_capital) / assumptions.initial_capital * 100
        ),
        max_drawdown_pct=_pct(max_drawdown),
        final_equity=_money(final_equity),
        open_quantity=account.quantity,
        open_avg_entry_price=account.entry_price,
    )


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
        f"每次下單數量固定 {format(assumptions.quantity.normalize(), 'f')} 單位，"
        "而且一次只持有一個部位，不加碼、不放空。",
        f"起始本金 {format(assumptions.initial_capital.normalize(), 'f')}，"
        "只用來換算報酬率與最大回撤，不會擋下任何一筆買進。",
        "風控閘門：回測「不」套用實際下單的風控（部位上限、單筆金額上限、本金上限、"
        "訊號冷卻、待確認單數上限），也不模擬停損／停利。那些規則都是對照你「現在」的"
        "持倉與時鐘判斷的，套進過去只會讓「策略不賺錢」和「當時上限設太低」混在一起。",
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
