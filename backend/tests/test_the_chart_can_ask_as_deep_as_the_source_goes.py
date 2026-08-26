"""圖表能問的深度，不可以比 app 自己宣告的「這個來源給得出幾根」還淺。

使用者的說法是「往前拉以往數據不會再讀取資料，只看到空的畫面」。前端那一半是
它從來沒有在使用者往前拉的時候再問一次（那一半在 PriceChart.test.tsx）；後端這
一半是**就算它問了，也問不深**：

    _MAX_BARS[YFINANCE][HOUR_1] = 3500   ← app 對外宣告「這個來源給得出 3500 根」
    MAX_CHART_BARS               = 1000   ← 圖表問超過 1000 根就 422

同一份程式裡的兩個數字，對同一個問題給了兩個答案。`/api/market/timeframes` 把
前者送到瀏覽器（`max_bars`），前端照著它往前拉，然後撞上後者。**而 422 對使用者
的樣子，就是往前拉之後那一片空白。**

深度變深不等於多一次請求：`_period_days()` 對盤中週期一律回那個來源的硬牆
（1 分線 7 天、小時線 730 天），所以問 300 根和問 3500 根是**同一個 range、同一
次 HTTP**，只差在回應裡的那個陣列切到哪裡。1000 這個上限擋不住任何一次上游請
求——它只擋得住使用者往前拉。
"""

import pytest

from app.api.routers.market import MAX_CHART_BARS
from app.enums import DataSource
from app.main import app
from app.services.market_data.base import Bar, Timeframe, max_bars_available
from app.services.market_data.service import MarketDataService, get_market_data_service

_DECLARED = [
    (source, timeframe, max_bars_available(source, timeframe))
    for source in DataSource
    for timeframe in Timeframe
    if max_bars_available(source, timeframe) > 0
]


class _Deep:
    """回得出任意深度的 K 棒，所以測到的是「問得到嗎」不是「上游有沒有」。"""

    data_source = DataSource.YFINANCE

    def get_quotes(self, symbols):
        return {}

    def get_bars(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Bar]:
        return []


@pytest.fixture
def deep():
    service = MarketDataService(providers={DataSource.YFINANCE: _Deep()})
    app.dependency_overrides[get_market_data_service] = lambda: service
    try:
        yield service
    finally:
        app.dependency_overrides.pop(get_market_data_service, None)


@pytest.mark.parametrize(("source", "timeframe", "declared"), _DECLARED)
def test_the_chart_may_ask_for_every_candle_the_source_declares(source, timeframe, declared):
    """兩個上限不可以互相矛盾。

    這一條刻意不打端點：它比對的是兩個常數。端點的 `le=` 是從 MAX_CHART_BARS 來
    的，所以只要這一條綠，`/bars` 和 `/indicators` 就都問得到宣告的深度；而只要
    有人調小 MAX_CHART_BARS 或調大 _MAX_BARS，紅的會是這一條，不是幾個月後某個
    使用者往前拉時看到的空白。
    """
    assert declared <= MAX_CHART_BARS, (
        f"{source.value} 的 {timeframe.value} 宣告給得出 {declared} 根，"
        f"但圖表最多只能問 {MAX_CHART_BARS} 根"
    )


def test_the_bars_endpoint_accepts_the_deepest_declared_depth(auth_client, deep):
    deepest = max(declared for _, _, declared in _DECLARED)

    response = auth_client.get(f"/api/market/bars?symbol=AAPL&timeframe=1h&limit={deepest}")

    assert response.status_code == 200


def test_the_indicator_endpoint_reaches_the_same_depth(auth_client, deep):
    """指標跟 K 棒是同一張圖上的兩層。

    只有 K 棒問得深，往前拉的結果是候選線在第 300 根就斷掉——一張看起來像
    「這個指標從這裡才開始存在」的圖，而那不是真的。
    """
    deepest = max(declared for _, _, declared in _DECLARED)

    response = auth_client.post(
        "/api/market/indicators",
        json={
            "symbol": "AAPL",
            "timeframe": "1h",
            "limit": deepest,
            "indicators": [{"name": "sma", "params": {"period": 20}}],
        },
    )

    assert response.status_code == 200
