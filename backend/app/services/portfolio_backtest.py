"""多支代號共用一份資金與風險設定。

#34 的第三項。前兩項是「同一支策略跑很多次」，這一項不一樣：**很多支代號共用一個
帳戶**，而共用帳戶會逼出三個單支回測從來不必回答的問題。三個的共通點是「答錯了也
看不出來」。

＊ 一、同一天兩支都要買，而錢只夠一支。

單支回測沒有這個問題：錢不夠就是不夠。共用帳戶就有先後順序，而**任何順序都是武斷
的**——重點不是選對，是選一個、說出來、而且每次都一樣。不說的話，使用者換一次代號
的排列就得到不同的績效，而他會以為那是策略的差別。

這裡選的是**他自己列出來的順序**。理由：那是他唯一看得見的東西，而按字母排會讓
「AAPL 優先於 TSLA」變成一條沒有人同意過的規則。

＊ 二、各支的 K 棒日期對不齊。

台股和美股的假日不同，個股也會停牌。共同時間軸取**聯集**不是交集：取交集會讓一支
常停牌的代號把其他每一支的交易日一起砍掉，而那個損失沒有任何地方看得到。

＊ 三、權益曲線要在哪個價格上加總。

那天沒有 K 棒的持股，只能用它最後一次的收盤價入帳——那是唯一能做的事。但它讓那條
曲線裡混著過期的價格，而曲線看起來跟真的一模一樣。所以每一個點都記下當天有哪幾支
是用舊價入帳的，跟這個 app 對「存下來的 K 棒」的處理一樣：能畫，但要標明。

＊ 訊號怎麼來的。

每一支各跑一次 replay（子行程，#18），拿回一串訊號，然後在這裡逐日推進。策略之間
互不相見——它們本來就看不到彼此，也看不到帳戶，所以這一點跟單支回測完全一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from app.services import strategy_pool
from app.services.backtest import BacktestAssumptions, BacktestSummary, score_signals
from app.services.market_data.base import Bar
from app.services.strategy_worker import StrategyWorkerError


class PortfolioError(Exception):
    """這個投組本身有問題，跑之前就知道。"""


@dataclass
class PortfolioPoint:
    timestamp: datetime
    cash: Decimal
    equity: Decimal
    # 這一天有哪幾支是用**上一次**的收盤價入帳的。空的才代表這個點完全新鮮。
    stale_symbols: list[str] = field(default_factory=list)


@dataclass
class PortfolioLeg:
    symbol: str
    trade_count: int = 0
    # 這一支在**投組裡**真的建倉幾次。
    opened: int = 0
    # 想買而錢不夠、因此沒買到的次數。
    #
    # 這是使用者問「共用錢包讓我付出什麼」時唯一該看的數字，而它在單支回測裡根本
    # 不存在。沒有它的話，一個因為排在後面而幾乎沒買到的代號，看起來會像一支「訊
    # 號很少」的爛策略。
    skipped_for_cash: int = 0
    summary: BacktestSummary | None = None
    note: str | None = None


@dataclass
class PortfolioResult:
    legs: list[PortfolioLeg] = field(default_factory=list)
    equity_curve: list[PortfolioPoint] = field(default_factory=list)
    summary: BacktestSummary | None = None
    notes: list[str] = field(default_factory=list)


def _signals_for(
    source_code: str,
    bars: list[Bar],
    stored_warmup_bars: int,
    assumptions: BacktestAssumptions,
) -> tuple[list[str], int, BacktestSummary | None, str | None]:
    """一支代號的訊號，加上它自己單獨跑的成績。

    每一支各自的成績也要留下來：只給總分的話，一支拖累整體的代號會被另一支蓋過
    去——而使用者要做的決定正是「哪一支該拿掉」。
    """
    try:
        replayed = strategy_pool.replay(source_code, {}, bars, stored_warmup_bars)
    except StrategyWorkerError as exc:
        return [], 0, None, f"這一支跑不起來：{exc}"

    if replayed["failed_at"] == -1:
        return [], 0, None, f"暖身階段就發生錯誤：{replayed.get('error')}"

    warmup = replayed["warmup"]
    signals = replayed["signals"]
    if replayed["failed_at"] is not None:
        index = replayed["failed_at"]
        at = bars[warmup + index].timestamp if warmup + index < len(bars) else None
        when = f"{at:%Y-%m-%d}" if at else "某一根"
        return signals, warmup, None, f"在 {when} 這根 K 棒發生錯誤：{replayed.get('error')}"

    scored = score_signals(bars=bars, warmup=warmup, signals_in=signals, assumptions=assumptions)
    return signals, warmup, scored.summary, None


def run(
    *,
    source_code: str,
    bars_by_symbol: dict[str, list[Bar]],
    stored_warmup_bars: int = 0,
    assumptions: BacktestAssumptions | None = None,
) -> PortfolioResult:
    """一份資金，多支代號，一條共同的時間軸。"""
    assumptions = assumptions or BacktestAssumptions()
    if not bars_by_symbol:
        raise PortfolioError("沒有要跑的代號。")

    result = PortfolioResult()
    # 使用者列出來的順序。dict 在 Python 3.7 之後保序，所以這就是他打的順序。
    order = list(bars_by_symbol)

    signals_by_symbol: dict[str, list[str]] = {}
    warmup_by_symbol: dict[str, int] = {}
    for symbol in order:
        bars = bars_by_symbol[symbol]
        if not bars:
            # 安靜地少一支，會讓使用者以為他問的那個投組跑過了——而其實跑的是另一
            # 個。這個 repo 已經被「空清單被讀成正常結果」咬過。
            result.legs.append(PortfolioLeg(symbol=symbol, note="抓不到這一支的歷史資料。"))
            continue
        signals, warmup, summary, note = _signals_for(
            source_code, bars, stored_warmup_bars, assumptions
        )
        signals_by_symbol[symbol] = signals
        warmup_by_symbol[symbol] = warmup
        result.legs.append(
            PortfolioLeg(
                symbol=symbol,
                trade_count=summary.trade_count if summary else 0,
                summary=summary,
                note=note,
            )
        )

    by_symbol = {leg.symbol: leg for leg in result.legs}
    live = [symbol for symbol in order if signals_by_symbol.get(symbol)]
    if not live:
        result.notes.append("沒有任何一支跑得出訊號，所以沒有績效可以算。")
        return result

    # 聯集，不是交集。取交集會讓一支常停牌的代號把其他每一支的交易日一起砍掉，而
    # 那個損失沒有任何地方看得到。
    timeline = sorted(
        {
            bar.timestamp
            for symbol in live
            for bar in bars_by_symbol[symbol][warmup_by_symbol[symbol] :]
        }
    )

    bar_at: dict[tuple[str, datetime], Bar] = {}
    signal_at: dict[tuple[str, datetime], str] = {}
    for symbol in live:
        tested = bars_by_symbol[symbol][warmup_by_symbol[symbol] :]
        for index, bar in enumerate(tested):
            bar_at[(symbol, bar.timestamp)] = bar
            if index < len(signals_by_symbol[symbol]):
                signal_at[(symbol, bar.timestamp)] = signals_by_symbol[symbol][index]

    cash = assumptions.initial_capital
    held: dict[str, Decimal] = dict.fromkeys(live, Decimal(0))
    entry: dict[str, Decimal] = dict.fromkeys(live, Decimal(0))
    last_close: dict[str, Decimal] = {}
    trades = 0
    stale_ever = False

    for moment in timeline:
        # 先賣後買：賣出來的錢當天就可以用。反過來的話，一次換股會因為「錢還沒回
        # 來」而買不到，而那不是真的——同一天賣掉的部位，券商的可用餘額就增加了。
        for symbol in live:
            bar = bar_at.get((symbol, moment))
            if bar is None:
                continue
            last_close[symbol] = Decimal(str(bar.close))
            if signal_at.get((symbol, moment)) == "SELL" and held[symbol] > 0:
                cash += held[symbol] * Decimal(str(bar.close))
                trades += 1
                held[symbol] = Decimal(0)
                entry[symbol] = Decimal(0)

        for symbol in live:
            bar = bar_at.get((symbol, moment))
            if bar is None or signal_at.get((symbol, moment)) != "BUY" or held[symbol] > 0:
                continue
            price = Decimal(str(bar.close))
            want = assumptions.quantity
            cost = want * price
            if cost > cash:
                # 錢不夠。**先到先得，而順序就是他自己列的那個。**
                by_symbol[symbol].skipped_for_cash += 1
                continue
            cash -= cost
            held[symbol] = want
            entry[symbol] = price
            by_symbol[symbol].opened += 1

        stale = [
            symbol for symbol in live if held[symbol] > 0 and bar_at.get((symbol, moment)) is None
        ]
        if stale:
            stale_ever = True
        equity = cash + sum(held[symbol] * last_close.get(symbol, Decimal(0)) for symbol in live)
        result.equity_curve.append(
            PortfolioPoint(timestamp=moment, cash=cash, equity=equity, stale_symbols=stale)
        )

    final = result.equity_curve[-1].equity if result.equity_curve else assumptions.initial_capital
    result.summary = BacktestSummary(
        bars_total=len(timeline),
        bars_tested=len(timeline),
        signals=sum(len(signals_by_symbol[symbol]) for symbol in live),
        skipped_signals=0,
        unfilled_signals=0,
        # **投組自己的成交數，不是各支單獨跑的總和。**
        #
        # leg.summary 是「這一支單獨用全額資金跑」的成績，那個數字有它的用處（要
        # 決定拿掉哪一支就得看它），但它跟投組裡實際發生的事不一樣：錢不夠的時
        # 候有人買不到，而那正是共用錢包要回答的問題。加總會把那個差額藏起來。
        trade_count=trades,
        wins=0,
        losses=0,
        stop_loss_exits=0,
        take_profit_exits=0,
        ambiguous_exit_bars=0,
        win_rate_pct=None,
        average_win=None,
        average_loss=None,
        net_pnl=final - assumptions.initial_capital,
        total_costs=Decimal(0),
        total_return_pct=(
            (final - assumptions.initial_capital) / assumptions.initial_capital * 100
            if assumptions.initial_capital
            else Decimal(0)
        ),
        max_drawdown_pct=Decimal(0),
        final_equity=final,
        open_quantity=sum(held.values()),
        open_avg_entry_price=Decimal(0),
        buy_and_hold_return_pct=None,
        excess_return_pct=None,
        profit_factor=None,
        exposure_pct=None,
    )

    result.notes.append(
        "**同一天多支都要買而錢不夠的時候，按你列出來的順序先到先得。** 任何順序都"
        "是武斷的，所以這裡選一個並且說出來——換一次代號的排列，結果就會不一樣，而"
        "那不是策略的差別。"
    )
    if stale_ever:
        result.notes.append(
            "**有幾天某些持股沒有報價**（假日不同、停牌），那幾天是用它最後一次的收"
            "盤價入帳的。權益曲線上那幾個點標了出來——它們不是錯的，但也不是當天的"
            "真實價格。"
        )
    return result
