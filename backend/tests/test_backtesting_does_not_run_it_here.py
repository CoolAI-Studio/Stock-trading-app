"""回測也不可以在 API 行程裡跑使用者的程式碼。

#18 已經搬走了盯盤迴圈和 /validate。回測是最後一處，而它有一個前兩處沒有的性質：
**它是唯一一個會把使用者的程式碼跑幾千次的地方。**

那讓兩件事變得比別處嚴重：

1. `_guarded` 給的是**每一根 K 棒**兩秒，整場回測沒有總期限。一支每根都卡住的策
   略，五千根就是兩小時多的請求執行緒，外加五千條殺不掉的執行緒——Python 殺不掉
   執行緒，這是 strategy_runtime._guarded 自己的檔頭寫的。
2. 回測是使用者最常按的按鈕，而它按下去的東西是自己寫的、還沒驗過的程式碼。

搬過去之後：整串訊號**一次往返**拿回來，總期限由父行程用殺行程執行。

＊ 為什麼不是把整個 run_backtest 搬進子行程。

票上原本這樣寫，但看清楚之後有更小的切法：`run_backtest` 裡只有三個地方碰使用者
的程式碼（warm_up、和逐根的 _dispatch），而 _dispatch 只餵一根 K 棒進去——策略看
不到模擬帳戶。所以把「跑出訊號」搬走就夠了，帳戶模擬和計分留在原地。

差別是序列化面：整段搬過去要讓 BacktestAssumptions 和 BacktestResult 都過一次
JSON，而那兩個是這個 app 裡欄位最多的東西。搬訊號只要「K 棒進去、字串出來」。一
次往返的目的是延遲，而這個切法一樣是一次往返。
"""

import threading
from datetime import UTC, datetime, timedelta

import pytest

from app.enums import DataSource
from app.main import app
from app.services import market_loop
from app.services.market_data.base import Bar, Timeframe
from app.services.market_data.service import MarketDataService, get_market_data_service

_START = datetime(2026, 1, 5, tzinfo=UTC)
_END = _START + timedelta(days=39)

STUCK_ON_BAR = """
class Strategy:
    def __init__(self):
        self.name = "stuck_bar"
        self.symbol = "2330.TW"
        self.timeframe = "1d"

    def on_bar(self, bar) -> str:
        while True:
            pass
"""

STUCK_IN_INIT = """
class Strategy:
    def __init__(self):
        self.name = "stuck_init"
        self.symbol = "2330.TW"
        self.timeframe = "1d"
        while True:
            pass

    def on_bar(self, bar) -> str:
        return "HOLD"
"""

RAISES_ON_THE_FOURTH_BAR = """
class Strategy:
    def __init__(self):
        self.name = "boom"
        self.symbol = "2330.TW"
        self.timeframe = "1d"
        # 宣告暖身只要兩根，那第四次呼叫就落在**被測**的那一段裡。不宣告的話預設
        # 是 30 根，第四次還在暖身，而暖身的錯誤是另一條訊息（沒有「哪一根」可以
        # 指）——那條路由下面另一條測試守。
        self.warmup_bars = 2
        self.seen = 0

    def on_bar(self, bar) -> str:
        self.seen += 1
        if self.seen == 4:
            raise ValueError("第四根就爆了")
        return "HOLD"
"""


class _StubBarProvider:
    """一段會漲的日線，只有 2330.TW。跟 test_backtest_api.py 同一個做法。"""

    def get_bars(self, symbol: str, timeframe: Timeframe, limit: int = 300) -> list[Bar]:
        if symbol != "2330.TW":
            return []
        return [
            Bar(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=_START + timedelta(days=day),
                open=100.0 + day,
                high=101.0 + day,
                low=99.0 + day,
                close=100.5 + day,
                volume=1000.0,
            )
            for day in range(40)
        ]

    def get_quotes(self, symbols, **kwargs):
        return {}


@pytest.fixture
def stub_market_data():
    service = MarketDataService(providers={DataSource.YFINANCE: _StubBarProvider()})
    app.dependency_overrides[get_market_data_service] = lambda: service
    try:
        yield service
    finally:
        app.dependency_overrides.pop(get_market_data_service, None)


@pytest.fixture(autouse=True)
def _short_timeouts(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "STRATEGY_BACKTEST_TIMEOUT_SEC", 3.0)


def _strategy_threads() -> list[str]:
    return [t.name for t in threading.enumerate() if t.name.startswith("strategy-")]


def _request(source_code: str) -> dict:
    return {
        "source_code": source_code,
        "symbol": "2330.TW",
        "start": _START.isoformat(),
        "end": _END.isoformat(),
    }


def test_a_strategy_that_never_returns_does_not_wedge_the_api(auth_client, stub_market_data):
    """一場回測要有**總**期限，不是每根 K 棒各一個。

    每根兩秒 × 五千根 = 兩小時多，而且每一根都留下一條殺不掉的執行緒。使用者看到
    的只是一個一直轉的圈圈，而那台機器只有 0.1 顆 CPU。
    """
    resp = auth_client.post("/api/backtests", json=_request(STUCK_ON_BAR))

    assert resp.status_code in (400, 422), resp.text
    assert not _strategy_threads(), "使用者的程式碼還在這個行程裡跑"
    assert not market_loop.stuck_children_still_running(), "子行程也沒被殺掉"


def test_a_strategy_that_hangs_in_its_constructor_is_caught_too(auth_client, stub_market_data):
    """建構式那條路是分開的，而且原本完全沒有期限。

    `_guarded` 只包 on_bar / on_tick，不包 __init__，而 compile_strategy 會執行類
    別主體和建構式。
    """
    resp = auth_client.post("/api/backtests", json=_request(STUCK_IN_INIT))

    assert resp.status_code in (400, 422), resp.text
    assert not _strategy_threads()
    assert not market_loop.stuck_children_still_running()


def test_an_error_mid_replay_still_names_the_candle(auth_client, stub_market_data):
    """搬家不可以把「是哪一根爆的」弄丟。

    這是使用者唯一的線索。一個沒有日期的「策略執行失敗」，對一個不是工程師的人
    等於沒有訊息——而這一段訊息在搬家之後是**父行程重建**出來的，因為例外送不過
    JSON 管線，所以特別容易掉。
    """
    resp = auth_client.post("/api/backtests", json=_request(RAISES_ON_THE_FOURTH_BAR))

    assert resp.status_code in (400, 422), resp.text
    detail = resp.json()["detail"]
    assert "第四根就爆了" in detail, detail
    assert "2026-01" in detail, f"錯誤訊息沒有說是哪一根 K 棒：{detail}"


def test_the_process_that_backtests_has_no_secrets(auth_client, stub_market_data, monkeypatch):
    """回測跑得最久、跑得最多次，所以它那個行程的環境更要是空的。"""
    from app.services import strategy_pool

    for name in ("SECRET_ENCRYPTION_KEY", "JWT_SECRET", "DATABASE_URL", "AI_API_KEY"):
        monkeypatch.setenv(name, f"real-{name.lower()}")

    strategy_pool.shutdown_scratch()
    auth_client.post("/api/backtests", json=_request(RAISES_ON_THE_FOURTH_BAR))

    seen = strategy_pool._scratch.child_environment()

    assert len(seen) <= 8, f"回測行程的環境有 {len(seen)} 個變數：{sorted(seen)}"
    for name in ("SECRET_ENCRYPTION_KEY", "JWT_SECRET", "DATABASE_URL", "AI_API_KEY"):
        assert name not in seen, f"{name} 進到跑回測的那個行程裡了"


BLOWS_UP_DURING_WARMUP = """
class Strategy:
    def __init__(self):
        self.name = "warm_boom"
        self.symbol = "2330.TW"
        self.timeframe = "1d"
        self.warmup_bars = 5
        self.seen = 0

    def on_bar(self, bar) -> str:
        self.seen += 1
        if self.seen == 2:
            raise ValueError("暖身就爆了")
        return "HOLD"
"""


def test_an_error_during_warmup_says_so(auth_client, stub_market_data):
    """暖身爆掉是另一條訊息，因為沒有「哪一根」可以指。

    暖身那幾根的訊號本來就全部丟掉——它們在這個實例存在之前就收盤了——所以指著
    其中一根說「這根有問題」會把使用者引到一個他不該看的地方。
    """
    resp = auth_client.post("/api/backtests", json=_request(BLOWS_UP_DURING_WARMUP))

    assert resp.status_code in (400, 422), resp.text
    detail = resp.json()["detail"]
    assert "暖身" in detail, detail
    assert "暖身就爆了" in detail, detail
