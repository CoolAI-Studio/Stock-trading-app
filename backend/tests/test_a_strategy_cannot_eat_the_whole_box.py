"""一支策略不可以把整台機器吃掉。

#18 的驗收條件裡最後沒做的一項：「CPU 與記憶體上限」。前面幾步買到的是**時間**
上的邊界（逾時就殺掉行程），這一項要的是**空間**上的：

    def on_bar(self, bar):
        self.junk = [0] * 10**10

這一行不會逾時——它跑得很快，只是要一百 GB。在有上限之前，它會讓作業系統開始換
頁，然後把整個容器拖垮：API 沒有回應、盯盤迴圈停擺、通知送不出去。**警告不能停擺
是這個產品的最高優先**，而一支策略就能讓它停擺。

＊ 平台差異，以及為什麼測試要這樣寫。

`resource.setrlimit` 只有 POSIX 有。線上是 Linux（Render／Docker 都是），開發機是
Windows。如果這裡寫成「每個平台都要有上限」，那在 CI 綠、在本機紅——而這個 repo
已經被「本機綠、CI 紅」咬過三次，反過來一樣糟。

所以分成兩層問：

1. **每個平台都要問的**：上限那段程式碼有被呼叫到，而且不管有沒有生效，子行程都
   要活著。一個「在 Windows 上會讓子行程起不來」的上限，等於在開發機上把整個策略
   功能關掉。
2. **只有 Linux 問的**：上限真的擋得住。標記成 skip 而不是不寫——不寫的話，線上
   唯一在乎的那件事就沒有任何東西守著。
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from app.services import strategy_pool
from app.services.strategy_worker import StrategyWorker

BACKEND = Path(__file__).resolve().parent.parent

GREEDY = """
class Strategy:
    def __init__(self):
        self.name = "greedy"
        self.symbol = "AAPL"

    def on_tick(self, current_price: float) -> str:
        # 8 億個整數約 800 MB：足以撞破 256 MB 的上限，但萬一上限沒生效
        # 也不會把跑測試的機器拖垮。10**10 那種數字才是策略真的會做的事，
        # 但測試不需要用它來證明同一件事。
        self.junk = [0] * (10 ** 8)
        return "HOLD"
"""


def test_the_child_says_what_limits_it_is_under():
    """子行程要說得出自己被限成什麼樣。

    一個宣稱有上限但沒有辦法檢查的上限，跟沒有上限的差別只在文件上——這跟
    child_environment() 存在的理由是同一個。
    """
    worker = StrategyWorker()
    worker.start()
    try:
        limits = worker.limits()
    finally:
        worker.close()

    assert "memory_bytes" in limits
    assert "applied" in limits


def test_the_child_starts_even_where_limits_do_not_exist():
    """Windows 沒有 resource 模組。那不可以讓子行程起不來。

    在開發機上起不來，等於在開發機上把整個策略功能關掉——而那正是每一個貢獻者
    寫程式的地方。
    """
    worker = StrategyWorker()
    worker.start()
    try:
        assert worker.ping() is True
    finally:
        worker.close()


@pytest.mark.skipif(sys.platform == "win32", reason="resource.setrlimit 只有 POSIX 有")
def test_the_limit_is_actually_applied_on_the_platform_that_ships():
    """線上跑的是 Linux，而線上是唯一在乎這件事的地方。"""
    worker = StrategyWorker()
    worker.start()
    try:
        limits = worker.limits()
    finally:
        worker.close()

    assert limits["applied"] is True
    assert 0 < limits["memory_bytes"] <= 512 * 1024 * 1024


@pytest.mark.skipif(sys.platform == "win32", reason="resource.setrlimit 只有 POSIX 有")
def test_a_greedy_strategy_is_an_error_not_a_dead_box():
    """要一百 GB 的策略，是一個講得出原因的錯誤，不是整台機器停擺。

    這一條是這一項驗收條件的全部意義。它不逾時——那一行跑得很快，只是要的記憶體
    比整台機器多。在有上限之前，作業系統會開始換頁，然後 API、盯盤迴圈和通知一起
    停下來。
    """
    from app.services.strategy_worker import StrategyWorkerError

    pool = strategy_pool.StrategyPool(size=1)
    try:
        strategy = pool.get_or_load(1, GREEDY)

        # 先確認上限這一輪真的在。**沒在就跳過，不要真的去配那 800 MB。**
        #
        # 上限可能因為載不起來而被退讓掉（見 lift_limits），而在那種情況下這條測
        # 試會變成一個「故意把跑測試的機器塞爆」的測試——那不是在驗任何東西，只是
        # 在製造一個看起來像記憶體不足的假失敗。
        if not pool._slots[0].worker.limits()["applied"]:
            pytest.skip("這台機器上的記憶體上限沒有套用成功，跳過而不是真的去配 800 MB")

        with pytest.raises(StrategyWorkerError):
            strategy.on_tick(100.0)

        # 而且下一支策略照樣跑得起來——貪心的那支只能弄倒自己那個行程。
        recovered = pool.get_or_load(
            2,
            "class Strategy:\n"
            "    def __init__(self):\n"
            "        self.name = 'fine'\n"
            "        self.symbol = 'AAPL'\n"
            "    def on_tick(self, price):\n"
            "        return 'HOLD'\n",
        )
        assert recovered.on_tick(100.0) == "HOLD"
    finally:
        pool.shutdown()


def test_setting_the_limit_never_raises():
    """上限設不起來只能是「沒設成」，不可以是「行程起不來」。

    在一個唯讀的容器、一個已經被外層 cgroup 限制過的環境、或一個 setrlimit 被
    seccomp 擋掉的沙箱裡，這一句都可能失敗。失敗的正確反應是繼續跑——**警告不能停
    擺優先於一個沒設成的上限**。

    直接在乾淨的子行程裡問，不繞過整個 worker：這一條要的是那個函式本身的性質。
    """
    code = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(BACKEND)!r})
        from app.services.strategy_worker_main import apply_limits

        # 一個一定設不起來的值（負數），照樣不可以拋。
        print(apply_limits(-1))
        print(apply_limits(64 * 1024 * 1024))
    """)
    done = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )

    assert done.returncode == 0, done.stderr[-2000:]


def test_a_limit_too_tight_to_load_the_sandbox_gets_lifted():
    """上限緊到連沙箱都載不起來的話，**寧可沒有上限也要活著**。

    這是這個功能唯一可以接受的失敗方式，而它值得一條專門的測試，因為那個後果是
    這個 repo 最不能接受的那一種：

    RLIMIT_AS 限的是位址空間不是 RSS，而 CPython 光是啟動就會 mmap 一大片——所以
    「256 MB 夠不夠」不是一個看得出來的數字，它取決於 glibc 的版本、執行緒堆疊、
    容器的設定。設得太緊，每一支策略都會永遠載不起來；而那被歸類成基礎設施問題
    （不停用策略，只留一句話），所以畫面上每一支都寫著「行程暫時不可用」，而它永
    遠不會好。**警告全面停擺**，比完全沒有上限糟得多。

    這一條在 Windows 上也跑得到，因為驗的是**退讓的邏輯**，不是平台的行為：把沙
    箱的 import 弄成一定丟 MemoryError，然後問「它有沒有拿掉上限再試一次」。
    """
    code = textwrap.dedent(f"""
        import sys, builtins
        sys.path.insert(0, {str(BACKEND)!r})
        from app.services import strategy_worker_main as child

        # 讓第一次 import 沙箱一定丟 MemoryError，第二次放行。
        real_import = builtins.__import__
        tries = []

        def fake_import(name, *args, **kwargs):
            if name == "app.services.strategy_runtime":
                tries.append(name)
                if len(tries) == 1:
                    raise MemoryError("模擬：位址空間不夠")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = fake_import
        try:
            compile_strategy = child._sandbox()
        finally:
            builtins.__import__ = real_import

        assert len(tries) == 2, f"沒有重試：{{tries}}"
        assert callable(compile_strategy)
        assert child._limits["applied"] is False
        assert "警告不能停擺" in child._limits["why"], child._limits
        print("OK")
    """)
    done = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )

    assert done.returncode == 0, done.stderr[-3000:]
    assert "OK" in done.stdout


# --- 每殺一次，不可以永久漏掉一個行程和兩個管線 -------------------------------
#
# 逾時的處置是**殺行程**（CLAUDE.md 的第二條不可退讓），而 _kill() 把被殺的那個
# Popen 放進模組級的 _abandoned 清單、從不關 stdin／stdout。唯一會修剪那份清單的
# abandoned_children_still_running() 在正式路徑上沒有呼叫端——grep 全 repo，只有測試
# 經由 market_loop.stuck_children_still_running 用到。
#
# 所以每一次策略逾時、每一次 /validate 打到跑不完的策略、每一次掃描裡卡住的那一組，
# 都在後端行程裡永久多留兩個 fd。而終點正是這個 repo 最不能接受的那一種：fd 耗光之後
# Popen 再也起不來，每一支策略永遠停在「策略行程暫時不可用」——那是全面停擺。


def test_killing_a_worker_closes_its_pipes():
    """殺掉之後，那兩個管線要關掉。

    行程本身被作業系統收走了，但父行程這一端的 fd 不會自己消失——它們活到整個後端
    重啟為止。
    """
    worker = StrategyWorker()
    worker.start()
    process = worker._process
    assert process is not None

    worker._kill()

    assert process.stdin is None or process.stdin.closed, "stdin 沒關"
    assert process.stdout is None or process.stdout.closed, "stdout 沒關"


def test_the_abandoned_list_does_not_grow_without_bound():
    """死掉的行程要從清單裡消失，而且不需要有人記得去呼叫回收。

    這份清單存在的理由是「讓無窮迴圈真的被殺掉了驗得到」，那是對的；但它同時變成一個
    只增不減的容器，而回收只在測試裡被呼叫過。
    """
    from app.services import strategy_worker as module

    before = len(module._abandoned)
    for _ in range(5):
        worker = StrategyWorker()
        worker.start()
        worker._kill()

    assert len(module._abandoned) <= before, (
        f"殺了 5 次之後清單多了 {len(module._abandoned) - before} 筆，而它們都已經死了"
    )
