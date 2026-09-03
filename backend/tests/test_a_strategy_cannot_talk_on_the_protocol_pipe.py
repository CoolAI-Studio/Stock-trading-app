"""策略 print 一次，不可以撞壞盯盤用的那條管線。

父子行程之間走的是一行一個 JSON（見 strategy_worker.py 的檔頭），而那條管線**就是
子行程的 stdout**。沙箱的安全 builtins 白名單裡有 print，所以使用者寫一句
`print('debug', price)`，寫出去的東西就直接插在協定中間。

父行程讀到那一行、json.loads 失敗，_read_line 判成 WorkerUnavailable 並殺掉整個子
行程。接下來連鎖的是這個 repo 最不能接受的那一種：

  * **同一格上的其他策略跟著死。** 它們的實例活在那個行程裡（那正是常駐子行程的
    全部意義），所以一支已經暖好二十根的均線策略記憶歸零，重新暖身期間只會回 HOLD。
  * **沒有人會被告訴。** WorkerUnavailable 被 market_loop 的 _record_strategy_error
    歸類成基礎設施問題：不累積錯誤、不停用，只在那一列留一句話。那條路本身是對的
    （子行程起不來不是使用者的錯），錯的是我們把協定放在使用者寫得到的地方。
  * **它不會自己好。** 策略每一輪都會再 print 一次，所以這是每五秒重演一次的迴圈。

修法不是把 print 從白名單拿掉——那又是一次列舉式的拒絕，而且會讓一支 print 過的策
略變成 NameError（使用者的程式碼沒有錯）。要換掉的是問題的形狀：子行程在跑任何使用
者程式碼之前，就把協定的兩端從 sys.stdout／sys.stdin 手上接管走，路的盡頭沒有管線。
"""

import contextlib

from app.services import strategy_pool
from app.services.strategy_worker import StrategyWorker, StrategyWorkerError

PRINTS_ON_EVERY_TICK = (
    "class Strategy:\n"
    "    def __init__(self):\n"
    "        self.name = '會印東西的'\n"
    "        self.symbol = 'AAPL'\n"
    "    def on_tick(self, price):\n"
    "        print('debug', price)\n"
    "        return 'HOLD'\n"
)

PRINTS_WHILE_COMPILING = (
    "class Strategy:\n"
    "    def __init__(self):\n"
    "        print('建構式也會跑')\n"
    "        self.name = '一出生就印東西的'\n"
    "        self.symbol = 'AAPL'\n"
    "    def on_tick(self, price):\n"
    "        return 'HOLD'\n"
)

COUNTS_TICKS = (
    "class Strategy:\n"
    "    def __init__(self):\n"
    "        self.name = '會累積的'\n"
    "        self.symbol = 'AAPL'\n"
    "        self.seen = 0\n"
    "    def on_tick(self, price):\n"
    "        self.seen += 1\n"
    "        return str(self.seen)\n"
)


def test_a_strategy_that_prints_still_gets_its_signal_back():
    """print 是使用者能寫的東西，所以它只能是「沒有人看得到」，不可以是「管線壞了」。"""
    # 20 秒不是 10 秒：第一次 load 要付沙箱 import 的錢（量到約 900 毫秒），而池子
    # 用 15、_scratch 用 20 都是刻意的。這裡不是在測速度。
    worker = StrategyWorker(timeout_sec=20.0)
    worker.start()
    try:
        worker.load("1", PRINTS_ON_EVERY_TICK)

        assert worker.on_tick("1", 100.0, timeout=10.0) == "HOLD"
        # 而且子行程還活著。這一條才是重點：一支策略印一行字，不可以等於那一格上
        # 的所有策略被砍掉重練。
        assert worker.ping() is True
    finally:
        worker.close()


def test_a_neighbours_print_does_not_wipe_the_warm_up_next_to_it():
    """鄰居印東西，不可以清掉同一格上別人累積的狀態。

    池子的檔頭承諾的是「一支卡住被殺掉，同格其他策略要重新暖身」——那是**逾時**換
    來的已知代價。print 不是逾時：它是一支跑得好好的策略，而它不該有能力去清掉隔
    壁那支的記憶。

    歸零的話這裡會拿到 "1"，而那正是使用者看不見的那一種壞掉：策略還開著、
    last_error 是 None，只是它退回去從頭暖身，這期間不會發出任何提醒。
    """
    pool = strategy_pool.StrategyPool(size=1)
    try:
        counter = pool.get_or_load(1, COUNTS_TICKS)
        noisy = pool.get_or_load(2, PRINTS_ON_EVERY_TICK)

        assert counter.on_tick(100.0) == "1"
        assert counter.on_tick(101.0) == "2"

        # 這裡不斷言 noisy 的回傳值——那是上面那條測試的工作。這一條要看的是**鄰
        # 居**，所以刻意讓 noisy 自己怎麼失敗都不影響，紅燈才會落在下一行的
        # "1" != "3" 上，也就是 docstring 講的那件事。
        with contextlib.suppress(StrategyWorkerError):
            noisy.on_tick(102.0)

        assert counter.on_tick(103.0) == "3", (
            "鄰居印了一行字，這支策略的累積狀態就沒了——子行程被殺掉重建，而畫面上"
            "它還開著、last_error 是 None。"
        )
    finally:
        pool.shutdown()


def test_a_print_while_compiling_does_not_break_it_either():
    """編譯就是執行：類別主體和 __init__ 都會跑，所以 print 也可能落在那裡。

    這條路比 tick 那條更容易被外面打到——/validate 只要登入就按得到，而它每一次都
    重新編一遍使用者當下貼上去的東西。
    """
    worker = StrategyWorker(timeout_sec=20.0)
    worker.start()
    try:
        detected = worker.validate(PRINTS_WHILE_COMPILING, [100.0, 101.0], timeout=20.0)

        assert detected["name"] == "一出生就印東西的"
        assert worker.ping() is True
    finally:
        worker.close()
