"""同一支策略、一整個參數網格，排出一張表。

#34 的第一項。宣告式參數（`StrategyParams`）已經做好了，這是它的下一步。

＊ 一張排名表的全部價值在於「可比」。

列與列之間的差異必須**只來自參數**。只要有任何一個其他變數跟著動，那張表就從
「哪組參數比較好」變成「哪組參數在哪批資料上比較好」——而後者看起來一模一樣，沒
有任何東西會說。

所以 K 棒由呼叫端抓好、抓一次，整個網格共用同一個 list。不是為了省頻寬：每組各抓
一次的話，就得有人保證那幾份是同一批，而「保證兩份資料一樣」是一個沒有人會去檢查
的條件。共用同一個物件就沒有那個條件。

＊ 一次往返。

N 組參數一起送進子行程，K 棒只送一次。一組一次往返的話，一千根 K 棒會被序列化 N
遍——而那正好是這個功能最容易變慢的地方。

＊ 掃描是過度配適的生產線，而這件事必須說出來。

在一整個網格上挑最高的那一格，挑到的通常是雜訊。程式擋不掉，但使用者一定要看到這
句話——票上的 walk-forward 正是為了這個而存在。交出一張排名表卻不說，等於在教他做
錯的事。
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass, field

from app.services import strategy_pool
from app.services.backtest import BacktestAssumptions, score_signals
from app.services.market_data.base import Bar
from app.services.strategy_worker import StrategyWorkerError

# 一次最多跑幾組。
#
# 不是效能的猜測，是**使用者在等一個 HTTP 回應**。每一組都要跑完整段 K 棒的使用者
# 程式碼，而超過這個數量的網格，通常代表他該先想清楚要問什麼問題，而不是把整個空
# 間都掃一遍——那也正是過度配適的形狀。
MAX_COMBINATIONS = 64


class SweepError(Exception):
    """這個網格本身有問題，跑之前就知道。"""


@dataclass
class SweepRow:
    params: dict
    summary: object | None = None
    error: str | None = None


@dataclass
class SweepResult:
    rows: list[SweepRow] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    truncated_note: str | None = None


def _combinations(grid: dict[str, list]) -> list[dict]:
    if not grid:
        # 空網格會跑一次預設值，然後看起來像「掃描完成」——而其實什麼都沒掃。
        raise SweepError("沒有要掃的參數。至少給一個參數和它要試的幾個值。")
    for name, values in grid.items():
        if not values:
            raise SweepError(f"參數 {name} 沒有給任何要試的值。")
    names = list(grid)
    return [dict(zip(names, combo, strict=True)) for combo in itertools.product(*grid.values())]


def run(
    *,
    source_code: str,
    bars: list[Bar],
    grid: dict[str, list],
    stored_warmup_bars: int = 0,
    assumptions: BacktestAssumptions | None = None,
    on_bars_used: Callable[[int], None] | None = None,
) -> SweepResult:
    """跑完整個網格，回一張表。

    `bars` 是**呼叫端抓好的那一份**，不是這裡去抓。這一點是刻意的：抓取是這張表唯
    一有可能在列與列之間變動的東西，而把它留在外面，「每一列看的是同一批」就變成
    一個看得見的事實，不是一個要靠註解維持的約定。
    """
    assumptions = assumptions or BacktestAssumptions()
    combos = _combinations(grid)

    # 參數名字**掃描之前**先問清楚，不要靠事後比對錯誤字串。
    #
    # 一個策略讀不到的參數，掃出來的每一列都會是同一個數字——而那看起來像「這個參
    # 數沒有影響」，一個完全合理、完全錯誤的結論。跟存策略時的 _check_params 同一
    # 個道理，只是這裡錯一次會錯 N 列。
    try:
        strategy_pool.check_params(source_code, combos[0])
    except StrategyWorkerError as exc:
        raise SweepError(str(exc)) from exc

    result = SweepResult()
    if len(combos) > MAX_COMBINATIONS:
        # 明講砍了幾組。這個 repo 已經被「靜默截斷」咬過一次（backtest 要兩萬根、
        # provider 給五年、差額被吞掉），同一個錯不可以換個地方再犯。
        result.truncated_note = (
            f"這個網格有 {len(combos)} 組，只跑了前 {MAX_COMBINATIONS} 組。"
            "縮小範圍再試一次，結果會比較看得懂。"
        )
        combos = combos[:MAX_COMBINATIONS]

    if on_bars_used is not None:
        on_bars_used(len(bars))

    try:
        answer = strategy_pool.sweep(source_code, combos, bars, stored_warmup_bars)
    except StrategyWorkerError as exc:
        raise SweepError(f"這份策略程式碼跑不起來，所以整個網格都掃不了：{exc}") from exc

    for row in answer["rows"]:
        params = row["params"]
        if row.get("error"):
            result.rows.append(SweepRow(params=params, error=row["error"]))
            continue
        scored = score_signals(
            bars=bars,
            warmup=row["warmup"],
            signals_in=row["signals"],
            assumptions=assumptions,
        )
        result.rows.append(SweepRow(params=params, summary=scored.summary))

    if answer.get("unrun"):
        # 沒回來的那幾組。子行程只知道自己跑到哪裡，是這邊才知道原本送了哪幾組
        # ——所以差額在這裡補成一列一列的「跑不完」，而不是變成一張少了幾列、看
        # 起來卻很完整的表。
        for params in answer["unrun"]:
            result.rows.append(
                SweepRow(params=params, error="這一組沒跑完（整場掃描的時間用完了）")
            )
        result.notes.append(
            "有幾組沒跑完，時間用完了。它們在表上標成「沒跑完」——**不是零分**，"
            "是沒有答案。縮小網格再試一次。"
        )

    result.notes.append(
        "**在一整個網格上挑最高的那一格，挑到的通常是雜訊。** 這張表說的是「這組參數"
        "在這段歷史上表現如何」，不是「它以後會表現如何」。組數越多，最好的那一列越"
        "可能只是運氣——過度配適就是這樣發生的。真的要用之前，拿它去跑一次滾動前進"
        "（walk-forward）。"
    )
    return result
