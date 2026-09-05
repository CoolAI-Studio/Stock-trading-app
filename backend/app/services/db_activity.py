"""這個行程上一次真的送出 SQL 是什麼時候，總共送了幾句。

＊ 為什麼要記這個。

免費方案的資料庫是照**醒著的時間**計費的，而它閒置 5 分鐘才休眠——所以「多久碰它一
次」直接決定這個月活不活得下去，比「跑了幾句查詢」重要得多。

而 2026-09-05 那一輪查得很辛苦，就是因為沒有這個數字：唯一能用的工具是延遲差和用量頁
上的日增量，兩個都要等、都很鈍，而且**分不出是誰在敲**。這一格分得出來：

    數字一直很小        → 這個行程自己在敲（開著的分頁、重連中的 socket、探測）
    一路長到接近輪詢間隔 → 不是這個行程，那就是資料庫服務自己或別的客戶端

＊ 為什麼在引擎那一層記。

因為要量的是「真的送出去的 SQL」，不是「哪個函式被呼叫了」。session 建起來不會連線
（是懶的），ORM 也可能從識別映射直接給答案——只有 `after_cursor_execute` 是資料庫真的
被碰到的那一刻。

這一格自己不碰資料庫（全部在記憶體裡），所以讀它不會把要量的東西弄髒。
"""

import time
from collections.abc import Callable

from sqlalchemy import event
from sqlalchemy.engine import Engine


class DatabaseActivity:
    """記兩件事：上一句 SQL 是什麼時候、總共幾句。

    刻意只有這兩個。要問的是頻率，而頻率是兩次取樣相減就有的——存一串歷史反而要決定
    留多久、佔多少記憶體，而那些決定都沒有人需要。
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._last: float | None = None
        self._count = 0

    def record(self) -> None:
        # 每一句 SQL 都會走這裡，所以只能做這兩件事。
        self._last = self._clock()
        self._count += 1

    @property
    def statements(self) -> int:
        return self._count

    @property
    def last_statement_age_sec(self) -> float | None:
        """一句都還沒送過的時候回 None，**不是 0**。

        0 會被讀成「剛剛才碰過」，剛好是相反的意思。「不知道」不可以顯示成一個看起來
        很正常的數字。
        """
        if self._last is None:
            return None
        return self._clock() - self._last


activity = DatabaseActivity()


def watch(engine: Engine) -> None:
    """把紀錄器掛到一個引擎上。每個引擎只要掛一次。"""

    @event.listens_for(engine, "after_cursor_execute")
    def _record(conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: ANN001
        # 查的是模組層的 activity（不是掛上去那一刻的那個），測試才換得掉。
        activity.record()
