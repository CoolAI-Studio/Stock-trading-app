"""Liveness bookkeeping for the background market-data worker, read by /healthz.

Kept in process memory rather than a table: the worker would otherwise write a
row several times a minute, forever, to record something only the health probe
reads -- and the app runs with --workers 1 (see run_forever), so the process
answering /healthz is the same process running the loop.

Timestamps are monotonic, not wall-clock: an NTP correction on the host must not
be able to make a healthy worker look hours stale, or a dead one look fresh.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class HeartbeatSnapshot:
    uptime_sec: float
    last_loop_age_sec: float | None
    last_poll_age_sec: float | None
    # Polls in a row that asked for prices and got none back. Separate from
    # last_poll_age_sec because the two failures are different: the loop can
    # keep turning perfectly while every fetch comes back empty, and that is
    # precisely the state that used to read as healthy -- the providers catch
    # everything and return {}, so nothing raised and the probe stayed green
    # through an outage that had silenced every alert in the system.
    consecutive_empty_polls: int
    # Per symbol, how long it has gone without a price -- seconds since its
    # last good one, or since it was first asked for if it has never had one.
    # Only symbols currently WITHOUT a price appear; a healthy one is absent
    # rather than present with a zero.
    #
    # The count above cannot see this. It clears on any one good price, so
    # nine working symbols hide the tenth that never resolves -- and every
    # threshold on that tenth has silently stopped evaluating.
    symbol_gap_sec: dict[str, float]
    # 每一支策略叫不動它的子行程多久了。
    #
    # **這張快照上沒有別的東西看得到這件事。** 迴圈照轉、輪詢照完成、每一個代號照
    # 樣有價——而一支策略都沒跑，所以一則提醒都沒發出。「子行程壞掉不是策略的錯」
    # （#18）是那個失敗刻意在策略那一列上安靜的理由；這一欄是讓它不要在**其他每一
    # 個地方**也一起安靜。
    #
    # 有預設值，所以舊的測試用關鍵字自己組這張快照仍然可以跑：空的就是「沒有東西
    # 卡住」，而那是誠實的讀法。
    strategy_blocked_sec: dict[int, float] = field(default_factory=dict)
    # 哪幾段（代號＋週期）的 K 棒抓不到，抓不到多久了。
    #
    # **這張快照上沒有別的東西看得到這件事。** 報價和 K 棒走的是上游不同的端點，所以
    # 「報價回得來、K 棒回不來」是一個真的組合：consecutive_empty_polls 是 0、
    # symbol_gap_sec 是空的、strategy_blocked_sec 也是空的——而每一支 on_bar 策略一則
    # 提醒都沒發出。
    #
    # 鍵是 "代號 週期"，因為同一個代號的日線好好的、週線抓不到是常見的形狀。
    bar_gap_sec: dict[str, float] = field(default_factory=dict)
    # 迴圈自己說它下一輪大概多久之後會來（秒）。None 代表還沒說過。
    #
    # **有這一格，健康檢查的門檻才不用寫死。** 輪詢間隔在開市和關市差了兩百倍（5 秒
    # vs 30 分鐘），而一個能容得下關市那一段的固定門檻，在開市時就慢得沒有意義——一支
    # 卡死的迴圈要半小時才被發現，而那是盤中。反過來寫死成開市的數字，則是每天半夜一
    # 封「worker 沒有在跑」，而 worker 好得很。
    #
    # 兩種寫法各壞一半，所以問迴圈本身：它剛剛決定要睡多久，健康檢查就照那個放寬。
    expected_gap_sec: float | None = None
    # 這個行程起來之前，有多久沒有任何行程在跑。None 代表沒有這種空白。
    #
    # **這張快照上其他每一欄都看不到這件事，而且結構上不可能看得到**：它們全都是這
    # 個行程自己的記憶，而這裡要講的正是「上一個行程已經不在了」那段時間。行程死掉
    # 的時候，那些欄位跟著一起歸零，所以醒來之後每一欄都是健康的——那八個小時在這張
    # 快照上不存在。
    #
    # 唯一還記得的東西在資料庫裡（market_quotes.fetched_at），所以這一欄由開機時去問
    # 一次得到，然後就固定在這裡不再變。
    slept_sec: float | None = None


class WorkerHeartbeat:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._started_at = clock()
        self._last_loop_at: float | None = None
        self._last_poll_at: float | None = None
        self._consecutive_empty_polls = 0
        # symbol -> monotonic time it has been priceless since.
        self._priceless_since: dict[str, float] = {}
        # 策略 id -> 它從什麼時候開始叫不動子行程（monotonic）。
        self._blocked_since: dict[int, float] = {}
        # "代號 週期" -> 它從什麼時候開始抓不到 K 棒（monotonic）。
        self._bar_gap_since: dict[str, float] = {}
        # 開機時回頭看到的那段空白（秒）。算一次，之後不變。
        self._slept_sec: float | None = None
        # 迴圈上一次決定要睡多久。健康檢查照這個放寬門檻。
        self._expected_gap: float | None = None

    def mark_loop(self) -> None:
        """The loop reached the top of an iteration, so it is not wedged."""
        self._last_loop_at = self._clock()

    def mark_poll_success(self) -> None:
        """A whole poll cycle finished without raising."""
        self._last_poll_at = self._clock()

    def mark_quotes_fetched(self) -> None:
        """Prices actually came back. One good fetch clears the run."""
        self._consecutive_empty_polls = 0

    def mark_quotes_empty(self) -> None:
        """We asked for prices and got none.

        Only called when symbols were actually requested -- an account with no
        strategies and no positions asks for nothing, and that is not an
        outage.
        """
        self._consecutive_empty_polls += 1

    def mark_symbols(self, asked: set[str], answered: set[str]) -> None:
        """Which symbols this poll asked for, and which came back with a price.

        Called on every tick, including ticks that asked for nothing: symbols
        the owner has stopped watching are forgotten here, and that is what
        makes 「delete the bad row」 an actual fix rather than a permanently
        angry probe.
        """
        now = self._clock()
        for symbol in asked - answered:
            # setdefault, so an ongoing gap keeps the time it started rather
            # than resetting on every poll -- which would keep it forever
            # under any threshold.
            self._priceless_since.setdefault(symbol, now)
        for symbol in list(self._priceless_since):
            if symbol in answered or symbol not in asked:
                del self._priceless_since[symbol]

    def mark_blocked_strategies(self, blocked: set[int]) -> None:
        """這一輪有哪幾支策略是因為子行程叫不動而沒跑成。

        **整組重寫，不是累加。** 沒被列出來的就是這一輪沒有這個問題——它跑成功了，或
        者根本沒輪到它（關市的時候 on_tick 策略不會被呼叫）。「沒有被問」不等於「壞
        掉」，這條規則跟 mark_symbols 是同一條，理由也一樣：一個半夜亂叫的警報器會被
        學會忽略，然後真的停擺那一次的信長得一模一樣。

        也刻意不用單一計數器。CLAUDE.md 記過同一個教訓兩次（consecutive_empty_polls
        一次成功就歸零，會被九個好的蓋掉第十個）：三格 worker 只死一格的時候，單一計
        數器會 1,0,1,0 永遠跨不過門檻，而那三分之一的提醒是真的停了。
        """
        now = self._clock()
        for strategy_id in blocked:
            # setdefault：持續中的故障留著它**開始**的時間，不是每一輪重新計時——
            # 重新計時的話任何門檻都永遠跨不過去。
            self._blocked_since.setdefault(strategy_id, now)
        for strategy_id in list(self._blocked_since):
            if strategy_id not in blocked:
                del self._blocked_since[strategy_id]

    def mark_bar_gaps(self, failed: set[str]) -> None:
        """這一輪有哪幾段（代號＋週期）的 K 棒抓不到。

        跟 mark_blocked_strategies 一樣是**整組重寫**，理由也一樣：沒被列出來的就是這
        一輪沒有這個問題（抓成功了，或者根本沒輪到它）。「沒有被問」不等於「壞掉」，
        不然使用者把那支策略停掉之後那一格永遠不會消失，而一個按不掉的紅燈會被學會忽
        略——然後真的停擺那一次的信長得一模一樣。
        """
        now = self._clock()
        for series in failed:
            # setdefault：持續中的故障留著它**開始**的時間。每一輪重新計時的話，五秒
            # 一輪的迴圈永遠跨不過十五分鐘的門檻。
            self._bar_gap_since.setdefault(series, now)
        for series in list(self._bar_gap_since):
            if series not in failed:
                del self._bar_gap_since[series]

    def expect_next_within(self, seconds: float) -> None:
        """迴圈剛決定要睡多久——健康檢查照這個算「多久沒動算不正常」。

        寫在睡之前，不是醒之後：探測最不巧的那一刻正好是在睡的中間。
        """
        self._expected_gap = seconds

    def mark_downtime(self, seconds: float) -> None:
        """開機時回頭看：這個行程起來之前，有多久沒有任何行程在跑。

        **只在開機時叫一次**，而且之後不會被清掉。那段空白已經過去了，但它照樣是一
        段沒有人在盯盤的時間——而下一輪輪詢就會把資料庫裡唯一記得這件事的那個欄位蓋
        掉，所以不在這裡留住就永遠問不到了。
        """
        self._slept_sec = seconds

    def snapshot(self) -> HeartbeatSnapshot:
        # The marks are written from the event loop thread and read from the
        # threadpool thread serving /healthz. Single float attribute reads and
        # writes are atomic under the GIL, so no lock is needed; a snapshot may
        # simply be a few milliseconds behind, which no threshold here cares
        # about.
        now = self._clock()
        return HeartbeatSnapshot(
            uptime_sec=now - self._started_at,
            last_loop_age_sec=None if self._last_loop_at is None else now - self._last_loop_at,
            last_poll_age_sec=None if self._last_poll_at is None else now - self._last_poll_at,
            consecutive_empty_polls=self._consecutive_empty_polls,
            symbol_gap_sec={s: now - since for s, since in self._priceless_since.items()},
            strategy_blocked_sec={sid: now - since for sid, since in self._blocked_since.items()},
            bar_gap_sec={s: now - since for s, since in self._bar_gap_since.items()},
            slept_sec=self._slept_sec,
            expected_gap_sec=self._expected_gap,
        )


# Built at import, i.e. at process start -- which is exactly what the /healthz
# startup grace measures against, since a Render spin-down restarts the process.
heartbeat = WorkerHeartbeat()
