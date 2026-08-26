"""POST /api/strategies/validate 也不可以在 API 行程裡跑使用者的程式碼。

test_sandbox_escape.py 的檔頭把這條路寫得很清楚，值得原樣抄過來：

    WHY IT IS THE WORST THING IN THE APP. POST /api/strategies/validate needs
    only a login: it compiles the code, runs on_tick, and puts the return value
    in `sample_signals` in the HTTP response.

那個檔案關掉的是**已知的每一條逃逸路徑**，而它自己也說了那不是答案：

    The module's own header already said it was not a security boundary and
    that the real answer is a separate process. That remains true and is not
    what this file delivers.

#18 第 3 步把盯盤迴圈搬進子行程了，但 /validate 沒有——而它才是那個只要登入就打
得到、而且會把回傳值放進 HTTP 回應裡的端點。門關了，這一扇還開著。

這一組驗的是**後果**，不是實作：一支永遠不返回的策略送進 /validate 之後，這個行
程裡不可以留下任何還在跑的東西。舊做法留得下來——strategy_runtime._guarded 的檔頭
誠實地寫著 Python 殺不掉執行緒，被放棄的呼叫會一直燒著一顆核心直到行程重啟。而那
是**這個 API 行程**的核心，在一台 0.1 顆 CPU 的機器上。
"""

import threading

import pytest

from app.services import market_loop

STUCK = """
class Strategy:
    def __init__(self):
        self.name = "stuck"
        self.symbol = "AAPL"

    def on_tick(self, current_price: float) -> str:
        while True:
            pass
"""

STUCK_IN_INIT = """
class Strategy:
    def __init__(self):
        self.name = "stuck-early"
        self.symbol = "AAPL"
        while True:
            pass

    def on_tick(self, current_price: float) -> str:
        return "HOLD"
"""

FINE = """
class Strategy:
    def __init__(self):
        self.name = "fine"
        self.symbol = "AAPL"
        self.prices = []

    def on_tick(self, current_price: float) -> str:
        self.prices.append(current_price)
        return "BUY" if len(self.prices) > 3 else "HOLD"
"""


@pytest.fixture(autouse=True)
def _short_timeout(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "STRATEGY_TICK_TIMEOUT_SEC", 1.0)


def _strategy_threads() -> list[str]:
    """這個行程裡還在跑的策略執行緒。

    名字來自 strategy_runtime._guarded：`strategy-{name}`。它在這裡出現，就表示使
    用者的程式碼是在 API 行程裡跑的。
    """
    return [t.name for t in threading.enumerate() if t.name.startswith("strategy-")]


def test_a_hanging_strategy_leaves_nothing_running_in_this_process(auth_client):
    """送一支跑不完的策略去驗證，這個行程不可以被它佔住。

    這一條在搬家之前是紅的，而且紅得很安靜：回應照樣是 200、`ok` 照樣是 false，
    使用者看到的東西一模一樣——只是那條 `while True` 從此活在 API 行程裡，直到有
    人重啟它。送十次就是十顆核心。
    """
    resp = auth_client.post("/api/strategies/validate", json={"source_code": STUCK})

    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert not _strategy_threads(), "使用者的程式碼還在這個行程裡跑"
    assert not market_loop.stuck_children_still_running(), "子行程也沒被殺掉"


def test_a_strategy_that_hangs_in_its_constructor_is_caught_too(auth_client):
    """卡在 __init__ 的那條路是分開的。

    compile_strategy 會**執行**類別主體和 __init__——所以「還沒跑 on_tick」不代表
    還沒跑使用者的程式碼。只守 on_tick 而放過建構式，等於守了一扇門，旁邊那扇開著。
    """
    resp = auth_client.post("/api/strategies/validate", json={"source_code": STUCK_IN_INIT})

    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert not _strategy_threads()
    assert not market_loop.stuck_children_still_running()


def test_validation_still_tells_the_form_everything_it_needed(auth_client):
    """搬家不可以把回答弄丟。

    這一條是回歸：表單靠 detected_name / detected_symbol / declared_params /
    entry_point 畫出來，而 sample_signals 是使用者按下驗證之後唯一看得到的證據。
    少一個欄位，畫面上就少一塊，而不會有任何東西變紅。
    """
    body = auth_client.post("/api/strategies/validate", json={"source_code": FINE}).json()

    assert body["ok"] is True
    assert body["detected_name"] == "fine"
    assert body["detected_symbol"] == "AAPL"
    assert body["entry_point"] == "on_tick"
    # 十個樣本價，前三個還在累積所以是 HOLD，之後才 BUY——狀態要真的跨呼叫活著，
    # 不然這一串會全部是 HOLD 而測試看起來還是綠的。
    assert body["sample_signals"][:3] == ["HOLD", "HOLD", "HOLD"]
    assert "BUY" in body["sample_signals"]


def test_a_broken_strategy_is_still_a_clean_answer(auth_client):
    """壞掉的程式碼要回一個講得出原因的錯，不是 500。"""
    body = auth_client.post(
        "/api/strategies/validate", json={"source_code": "this is not python !!!"}
    ).json()

    assert body["ok"] is False
    assert body["error"]


def test_the_process_that_validates_has_no_secrets_either(auth_client, monkeypatch):
    """驗證用的是另一個 worker，所以它的環境要另外驗一次。

    test_the_strategy_process_has_no_secrets.py 驗的是盯盤那個池；這裡的
    `_scratch` 是模組層級的另一條路，而**它才是陌生人按一顆按鈕就打得到的那個**。
    同一個類別不等於同一條路徑會被走到——漏掉的話不會有任何東西變紅。
    """
    from app.services import strategy_pool

    for name in ("SECRET_ENCRYPTION_KEY", "JWT_SECRET", "DATABASE_URL", "AI_API_KEY"):
        monkeypatch.setenv(name, f"real-{name.lower()}")

    strategy_pool.shutdown_scratch()  # 用剛剛塞好的環境重新起一個
    auth_client.post("/api/strategies/validate", json={"source_code": FINE})

    seen = strategy_pool._scratch.child_environment()

    assert len(seen) <= 8, f"驗證行程的環境有 {len(seen)} 個變數：{sorted(seen)}"
    for name in ("SECRET_ENCRYPTION_KEY", "JWT_SECRET", "DATABASE_URL", "AI_API_KEY"):
        assert name not in seen, f"{name} 進到驗證使用者程式碼的那個行程裡了"
