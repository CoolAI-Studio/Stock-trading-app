"""策略跑在一個沒有秘密可拿的行程裡。

目前擋住 `os.environ` 的是 AST 靜態掃描加模組代理，而 strategy_runtime.py 自己的
檔頭就寫著那**不是安全邊界**——兩者都是「列舉式的拒絕」，漏一個名字就破。

這一組測的是另一件事：**那個東西不在**。

    現在：他找不到路
    做完：路的盡頭沒有東西

為什麼一定要 spawn 而不是 fork（Linux 的預設）：fork 出來的子行程繼承父行程的整
個位址空間。就算在子行程裡把 os.environ 清空，`app.config.settings` 這個已經載入
記憶體的物件照樣握著 SECRET_ENCRYPTION_KEY 和 JWT_SECRET 的字串。**清環境變數對
fork 沒有意義**，所以兩邊都強制 spawn，並且用 subprocess 明確指定一份乾淨的環境
（multiprocessing 的 spawn 仍然會繼承環境變數，這也是不用它的原因）。

而 Windows 本來就只有 spawn。明確指定，是為了讓我這台機器跟 CI、跟線上跑的是同一
條路徑——這個專案已經被「本機綠、CI 紅」咬過三次。
"""

import pytest

from app.services import strategy_worker

# 這幾個名字如果出現在子行程裡，就是這張票沒做到。
SECRETS = ("SECRET_ENCRYPTION_KEY", "JWT_SECRET", "DATABASE_URL", "TV_WEBHOOK_SECRET", "AI_API_KEY")


@pytest.fixture
def worker():
    w = strategy_worker.StrategyWorker()
    w.start()
    try:
        yield w
    finally:
        w.close()


def test_the_child_process_starts(worker):
    """先確認它真的活著，不然下面每一條都會因為「什麼都沒有」而假綠。"""
    assert worker.ping() is True


def test_no_secret_reaches_the_child(worker, monkeypatch):
    """父行程有這些值，子行程一個都拿不到。"""
    for name in SECRETS:
        monkeypatch.setenv(name, f"real-{name.lower()}-value")

    seen = worker.child_environment()

    for name in SECRETS:
        assert name not in seen, f"{name} 進到策略跑的那個行程裡了"


def test_the_child_environment_is_tiny(worker):
    """不是「拿掉幾個已知的名字」，是「只留下必要的幾個」。

    黑名單會漏。今天列了五個秘密，明天多一個環境變數就又漏一個——而漏掉的那次不
    會有任何東西變紅。所以這裡驗的是白名單的性質：子行程的環境小到可以整個列出來。
    """
    seen = worker.child_environment()

    assert len(seen) <= 8, f"子行程的環境有 {len(seen)} 個變數，太多了：{sorted(seen)}"


def test_a_strategy_actually_runs_in_there(worker):
    """隔離了但不能用，等於沒做。"""
    source = (
        "class Strategy:\n"
        "    def __init__(self):\n"
        "        self.name = 'in the worker'\n"
        "        self.symbol = '2330.TW'\n"
        "        self.seen = 0\n"
        "    def on_tick(self, price):\n"
        "        self.seen += 1\n"
        "        return 'buy' if price > 100 else 'hold'\n"
    )

    info = worker.compile(source)

    assert info["entry_point"] == "on_tick"


def test_bad_source_comes_back_as_a_clean_error_not_a_crash(worker):
    """壞掉的策略是一個錯誤訊息，不是一個死掉的 worker。

    如果它把 worker 弄死，那麼「一支策略寫壞」就會變成「所有策略停擺」——而盯盤
    不能停是這個產品唯一的鐵律。
    """
    with pytest.raises(strategy_worker.StrategyWorkerError):
        worker.compile("this is not python at all !!!")

    # 而且 worker 還活著。
    assert worker.ping() is True
