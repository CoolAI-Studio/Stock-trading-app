"""滾動前進：用前一段挑出來的參數，在它沒看過的下一段上還成立嗎。

#34 的第二項，也是參數掃描那句警語的解藥。

＊ 兩者問的是不同的問題。

    掃描      這組參數在**這段歷史**上表現如何
    滾動前進  這組參數在**它沒看過的資料**上表現如何

第一個問題的答案永遠找得到——網格夠大，總有一格好看。第二個問題才是使用者真正想知
道的，而它會拒絕大部分的答案。

＊ 這個模組唯一的失效模式：讓參數看到未來。

只要驗證區間的任何一根 K 棒參與過挑選，整件事就退化成一次比較貴的掃描——而**結果
會變好看**，所以沒有人會發現。底下每一個看起來多餘的邊界檢查都是為了這一件事。

三個漏法，三個都不會報錯：

  一、切分多給一根。`bars[:split + 1]` 跟 `bars[:split]` 在畫面上長得一樣。
  二、暖身從驗證區間裡拿。暖身不產生訊號，很容易被當成「不算數」——但暖身餵進去的
      價格決定了第一根被測 K 棒的指標值，那就是未來在決定現在。
  三、每一段各自抓一次 K 棒。兩次抓取之間上游做了一次除權調整，訓練段和驗證段就跑
      在兩組不同的價格上，而兩邊各自看起來都很正常。

第三點的解法跟參數掃描一樣：K 棒由呼叫端抓好、抓一次，所有段共用同一個 list。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.services import param_sweep, strategy_pool
from app.services.backtest import BacktestAssumptions, BacktestSummary
from app.services.market_data.base import Bar
from app.services.strategy_worker import StrategyWorkerError


class WalkForwardError(Exception):
    """切不出任何一段，或這個網格本身有問題。"""


@dataclass
class Fold:
    """一段：拿哪一截挑參數、拿哪一截驗證。

    兩個區間都是 `[start, end)` 的索引對，指向**同一個** bars list。用索引而不是各
    自持有一份切片，是刻意的：切片會讓「這兩段是同一批資料」變成一件要靠註解維持的
    事，而索引讓它變成看得見的。
    """

    index: int
    train: tuple[int, int]
    test: tuple[int, int]
    # 被測的那一段開始暖身的位置。必須小於 test[0]——暖身也是資料，也會洩漏。
    warmup_from: int = 0
    chosen_params: dict = field(default_factory=dict)
    train_summary: BacktestSummary | None = None
    test_summary: BacktestSummary | None = None
    note: str | None = None

    @property
    def test_range(self) -> tuple[int, int]:
        return self.test


@dataclass
class WalkForwardReport:
    folds: list[Fold] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def split(*, total: int, train: int, test: int, step: int) -> list[Fold]:
    """切出每一段的索引。

    只做算術，不碰資料——所以「有沒有重疊」這件事測得到，而且測起來很便宜。這個功
    能唯一會出錯的地方就在這幾行裡。
    """
    if train <= 0 or test <= 0 or step <= 0:
        raise WalkForwardError("訓練、驗證和前進的長度都必須大於零。")

    folds: list[Fold] = []
    start = 0
    while start + train + test <= total:
        train_range = (start, start + train)
        test_range = (start + train, start + train + test)
        folds.append(Fold(index=len(folds), train=train_range, test=test_range))
        start += step
    return folds


def _best(rows: list[param_sweep.SweepRow]) -> param_sweep.SweepRow | None:
    """訓練段上最好的那一組。

    用淨損益排，而且**跑不完的那幾組不參加排序**——它們沒有答案，不是零分。當成零
    分的話它們會沉到最底下，看起來像一個結論。
    """
    scored = [row for row in rows if row.summary is not None]
    if not scored:
        return None
    return max(scored, key=lambda row: row.summary.net_pnl)


def run(
    *,
    source_code: str,
    bars: list[Bar],
    grid: dict[str, list],
    train: int,
    test: int,
    step: int,
    stored_warmup_bars: int = 0,
    assumptions: BacktestAssumptions | None = None,
    on_bars_used: Callable[[int], None] | None = None,
) -> WalkForwardReport:
    """每一段各挑一次參數，然後拿它沒看過的下一截去驗證。"""
    assumptions = assumptions or BacktestAssumptions()

    # 這支策略自己說它要幾根暖身。問一次，因為那個數字決定了每一段要往回借幾根。
    #
    # 非問不可：effective_warmup 的規則是「原始碼宣告的贏」，所以呼叫端傳什麼進去
    # 不一定算數。不問而直接假設的話，一支宣告 warmup_bars = 0 的策略會讓那幾根本
    # 該當暖身的 K 棒被算進成績——而它們來自訓練期，於是樣本外的分數混進了樣本內的
    # 資料，看起來卻完全正常。
    try:
        described = strategy_pool.describe(source_code)
    except StrategyWorkerError as exc:
        raise WalkForwardError(f"這份策略程式碼跑不起來，所以切不了段：{exc}") from exc
    declared = described.get("warmup_bars")
    warmup = int(declared if declared is not None else stored_warmup_bars)

    folds = split(total=len(bars), train=train, test=test, step=step)
    if not folds:
        # 空報告看起來像「跑完了，沒什麼發現」，而其實是根本沒跑。這個 repo 已經被
        # 同一種安靜的空結果咬過（抓不到 K 棒被讀成「還在暖身」）。
        raise WalkForwardError(
            f"歷史不夠切：有 {len(bars)} 根 K 棒，而一段要 {train} 根訓練加 {test} 根驗證。"
            "把區間拉長，或把訓練／驗證的長度調小。"
        )

    if on_bars_used is not None:
        on_bars_used(len(bars))

    report = WalkForwardReport()
    for fold in folds:
        train_bars = bars[fold.train[0] : fold.train[1]]
        swept = param_sweep.run(
            source_code=source_code,
            bars=train_bars,
            grid=grid,
            stored_warmup_bars=stored_warmup_bars,
            warmup_override=min(warmup, max(0, len(train_bars) - 1)),
            assumptions=assumptions,
        )
        winner = _best(swept.rows)
        if winner is None:
            fold.note = "這一段沒有任何一組參數跑得完，所以沒有東西可以拿去驗證。"
            report.folds.append(fold)
            continue

        fold.chosen_params = winner.params
        fold.train_summary = winner.summary

        # 驗證段的暖身**從訓練那一側拿**。
        #
        # 暖身不產生訊號，所以很容易被當成「不算數」——但暖身餵進去的價格決定了第
        # 一根被測 K 棒的指標值。從驗證區間裡拿，就是未來的資料在決定現在的訊號，
        # 而分數會變好看。
        fold.warmup_from = max(0, fold.test[0] - warmup)
        borrowed = fold.test[0] - fold.warmup_from
        if borrowed < warmup:
            # 第一段可能借不滿。說出來——借得不夠的那幾根，指標算出來的值跟後面幾
            # 段不是同一回事，而那會讓第一段的分數看起來莫名其妙地差。
            fold.note = (
                f"這一段只借得到 {borrowed} 根暖身（要 {warmup} 根），指標的前幾個值不完整。"
            )
        validated = param_sweep.run(
            source_code=source_code,
            bars=bars[fold.warmup_from : fold.test[1]],
            grid={name: [value] for name, value in winner.params.items()},
            # **明確畫線**：被測的剛好是 test 那一段，借來的那幾根只暖身、不計分。
            warmup_override=borrowed,
            assumptions=assumptions,
        )
        out = _best(validated.rows)
        if out is None:
            fold.note = "挑出來的那一組在驗證區間上跑不完。"
        else:
            fold.test_summary = out.summary
        report.folds.append(fold)

    report.notes.append(
        "**看的是訓練段和驗證段之間的落差，不是任何一個單獨的數字。** 訓練段上好看是"
        "應該的——那組參數就是在那段資料上挑出來的。真正的問題是它在沒看過的資料上"
        "還剩多少。"
    )
    picked = [
        tuple(sorted(fold.chosen_params.items())) for fold in report.folds if fold.chosen_params
    ]
    if len(set(picked)) > 1:
        report.notes.append(
            "**每一段挑出來的參數不一樣。** 那代表這個參數沒有一個穩定的最佳值，而"
            "這個發現比任何一段的分數都重要——用固定一組去跑，等於在賭下一段剛好像"
            "其中某一段。"
        )
    return report
