"""調參頁讀的每一個欄位，後端都真的回得出來。

前端的 TypeScript 介面和後端的 Pydantic schema 之間**沒有任何連結**。後端把
`skipped_for_cash` 改個名字，前端那一格就變成空白——而三關 CI 全都會綠：後端的測
試看的是後端的欄位，前端的測試看的是它自己寫的替身。

這正是 CLAUDE.md 記下的那件事的另一種形狀：「替身和 jsdom 都看不到『選項被吃進去
了但行為不如預期』」。那次是圖表，這次是欄位名。

所以這裡從**真的跑起來的 app** 拿 OpenAPI schema，逐一比對前端讀的那些鍵。不是比
對整個 schema——那會讓每一次加欄位都要改測試——只比對前端真的會去讀的那幾個。
"""

from app.schemas.backtest import (
    BacktestSummaryRead,
    PortfolioLegRead,
    SweepResultRead,
    SweepRowRead,
    WalkForwardFoldRead,
)
from app.schemas.strategy import StrategyDetail

# 前端 TuningPage.tsx 裡的介面，逐字抄過來。
# 抄而不是 import，是刻意的：這份清單要跟著前端改，而它改了這裡就該紅。
SWEEP_ROW = {"params", "summary", "error"}
SWEEP_RESULT = {"symbol", "timeframe", "bars_total", "first_bar_at", "last_bar_at", "rows", "notes"}
SUMMARY_FIELDS = {"trade_count", "win_rate_pct", "net_pnl", "total_return_pct", "max_drawdown_pct"}
FOLD = {
    "index",
    "train_from",
    "train_to",
    "test_from",
    "test_to",
    "chosen_params",
    "train_summary",
    "test_summary",
    "note",
}
PORTFOLIO_LEG = {"symbol", "summary", "opened", "skipped_for_cash", "note"}


def _properties(model) -> set[str]:
    """這個 schema 實際會回出去的欄位名。

    直接問 Pydantic 模型，不繞 /openapi.json——那個端點預設是關的
    （ENABLE_API_DOCS=False，刻意的），而一條「因為文件關掉所以什麼都沒驗到」的測
    試比沒有測試更糟：它是綠的。
    """
    return set(model.model_fields)


def test_the_sweep_response_has_what_the_table_renders():
    assert SWEEP_RESULT <= _properties(SweepResultRead)
    assert SWEEP_ROW <= _properties(SweepRowRead)


def test_the_summary_has_the_columns_the_table_shows():
    """表格畫五欄，五欄都要有來源。少一個就是一格永遠空白的欄位。"""
    assert SUMMARY_FIELDS <= _properties(BacktestSummaryRead)


def test_the_walk_forward_response_puts_both_summaries_on_the_fold():
    """train_summary 和 test_summary 要在**同一個**物件上。

    分在兩個地方的話，前端就得自己把它們配對起來——而配錯了不會有任何東西說，那張
    表只是變得沒有意義。
    """
    assert FOLD <= _properties(WalkForwardFoldRead)


def test_the_portfolio_leg_still_reports_what_sharing_the_wallet_cost():
    """`skipped_for_cash` 是共用錢包唯一新增的資訊。

    它如果消失，投組頁看起來還是很正常——只是那一欄永遠是空的，而使用者要做的決定
    正是靠它。
    """
    assert PORTFOLIO_LEG <= _properties(PortfolioLegRead)


def test_the_strategy_detail_still_carries_the_source():
    """調參頁靠它拿原始碼去問可調參數。

    列表**刻意**不帶原始碼（儀表板會一直輪詢它），所以這條路只有 detail 走得通。
    detail 哪天也拿掉的話，調參頁就再也畫不出任何欄位——而它不會報錯，只會顯示
    「這支策略沒有宣告可調的參數」，一句完全合理的假話。
    """
    assert "source_code" in _properties(StrategyDetail)
