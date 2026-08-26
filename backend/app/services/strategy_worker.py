"""使用者的策略跑在這裡——一個沒有秘密可拿的子行程。

strategy_runtime.py 的 AST 掃描和模組代理擋住的是「拿得到 os.environ 的路」，而它
自己的檔頭就寫著那不是安全邊界：兩者都是列舉式的拒絕，漏一個名字就破。這個模組換
掉問題的形狀——

    現在：他找不到路
    做完：路的盡頭沒有東西

＊ 為什麼是 subprocess，不是 multiprocessing。

multiprocessing 在 Linux 上預設 fork，而 fork 出來的子行程繼承父行程的整個位址空
間。就算在子行程裡把 os.environ 清空，`app.config.settings` 這個已經載入記憶體的
物件照樣握著 SECRET_ENCRYPTION_KEY 和 JWT_SECRET 的字串——**清環境變數對 fork 沒
有意義**。改成 spawn 也不夠：multiprocessing 的 spawn 仍然會把父行程的環境變數整
份傳給子行程。

subprocess.Popen 可以明確指定 `env=`，那是唯一能保證「子行程只拿到我給的那幾個」
的做法。而 Windows 本來就只有 spawn，所以這個選擇同時讓我的機器、CI 和線上跑的是
同一條路徑——這個專案已經被「本機綠、CI 紅」咬過三次。

＊ 為什麼是常駐的行程，不是每次呼叫開一個。

量到的（見 #18 第 1 步的 commit）：

    IPC 往返                0.13 ms
    子行程啟動（搬 enums 後） 約 300 ms

每呼叫一次開一個行程，回測會從幾秒變成幾十分鐘；而策略的狀態（`self.prices`）本
來就得跨 tick 活著，那正是常駐行程給的東西。

＊ 協定：一行一個 JSON。

不用 pickle：pickle 會反序列化成任意物件，而這條管線的另一端跑的正是不受信任的程
式碼。JSON 只有資料，沒有行為。
"""

import contextlib
import json
import os
import subprocess  # nosec B404  # 見下面 Popen 的理由：env= 是這張票的核心手段
import sys
import threading
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

# 子行程只拿得到這幾個。
#
# 白名單而不是黑名單，這一點是刻意的：黑名單今天列了五個秘密，明天多一個環境變數
# 就又漏一個，而漏掉的那一次不會有任何東西變紅。
#
# PATH 是 Python 自己要用的；SYSTEMROOT 在 Windows 上不給就起不來（socket 和
# ctypes 都會炸）；PYTHONIOENCODING 是因為策略的錯誤訊息可能有中文，而 Windows 的
# 預設編碼會把它變成一個看不懂的 UnicodeEncodeError。
_KEEP_FROM_PARENT = ("PATH", "SYSTEMROOT", "SystemRoot", "TEMP", "TMP")


class StrategyWorkerError(Exception):
    """子行程回報的失敗，或跟它溝通時的失敗。

    底下分成兩種，而且**這個區分不是分類學上的潔癖，是這個產品的鐵律**。

    market_loop 的 _record_strategy_error 連續五次就把策略停用，而輪詢五秒一次。
    如果「子行程起不來」走那條路，那麼一次 spawn 失敗（記憶體不夠、容器剛重啟、
    唯讀檔案系統）在二十五秒之後就會把使用者**每一支**策略都停用——而且擋單結束
    之後沒有任何東西會把它們打開。畫面上只會寫著「停用」，沒有人看得出來為什麼。

    這正是 _record_feed_problem 上方那段註解已經記取過的教訓，只是換了一個來源。
    """


class WorkerUnavailable(StrategyWorkerError):
    """子行程起不來、死掉了，或管線上的訊息壞掉了。

    **不是策略的錯**，所以不累積錯誤次數、不停用策略。跟抓不到行情同一類：會自己
    好的事情，只在那一列上留一句話。
    """


class StrategyTimedOut(StrategyWorkerError):
    """策略在期限內沒有回來。

    **是策略的錯。** 一支永遠不返回的策略不會自己好，所以它走停用那條路——而這一
    次「停用」是真的做得到的：子行程殺得掉，那個無窮迴圈不會留下來燒 CPU。
    """


def _child_environment() -> dict[str, str]:
    env = {name: os.environ[name] for name in _KEEP_FROM_PARENT if name in os.environ}
    # 子行程要 import 得到 app.*，而不能靠繼承來的 PYTHONPATH（那可能指向別的東
    # 西，也可能根本沒有）。
    env["PYTHONPATH"] = str(BACKEND_ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    # 不寫 .pyc：子行程是短命的，而在唯讀的容器檔案系統上寫不進去會噴警告。
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


# 每一個被殺掉的子行程都留在這裡，直到確認它真的不在了。
#
# 存在的理由只有一個：讓「無窮迴圈真的被殺掉了」**驗得到**。舊做法的檔頭誠實地寫
# 著它做不到（Python 殺不掉執行緒，被放棄的呼叫會一直燒著一顆核心直到行程重啟），
# 而這張票宣稱換掉了那件事——一個宣稱做到了卻沒有辦法檢查的改善，跟沒改的差別只在
# 文件上。
_abandoned: list[subprocess.Popen] = []


def abandoned_children_still_running() -> list[int]:
    """被殺掉之後還活著的子行程 PID。正常情況下永遠是空的。"""
    global _abandoned
    _abandoned = [process for process in _abandoned if process.poll() is None]
    return [process.pid for process in _abandoned]


class StrategyWorker:
    """一個常駐子行程的把手。

    每一個方法都有逾時，而且**沒有任何一個會無限等待**。這不是防禦性程式設計的裝
    飾：這條管線的另一端跑的是使用者寫的迴圈，而呼叫端是盯盤的迴圈——那是這個產品
    唯一不能停的東西。
    """

    def __init__(self, *, timeout_sec: float = 10.0) -> None:
        self._timeout = timeout_sec
        self._process: subprocess.Popen | None = None
        # 一次只有一個請求在管線上。IPC 是 0.13 毫秒的事，排隊的成本遠小於為每一
        # 個策略開一條管線的複雜度。
        self._lock = threading.Lock()

    # --- 生命週期 ---------------------------------------------------------

    def start(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        # 下一行的每一個字都是常數：sys.executable 是正在跑的直譯器，模組名寫死在
        # 這裡，沒有 shell，而且沒有任何一段來自使用者——使用者的程式碼是**之後**
        # 經由 stdin 以 JSON 送進去的資料，不是命令列。
        #
        # 而這個 Popen 存在的理由本身就是安全性：只有它能用 env= 明確指定子行程拿
        # 得到哪幾個環境變數。multiprocessing 的 fork 會繼承整個位址空間，它的
        # spawn 會繼承環境變數——兩個都做不到這件事。
        self._process = subprocess.Popen(  # nosec B603  # 見上方：引數全是常數，且 env= 正是這張票的目的
            [sys.executable, "-m", "app.services.strategy_worker_main"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_child_environment(),
            cwd=str(BACKEND_ROOT),
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        ready = self._read_line(self._timeout)
        if ready.get("ready") is not True:
            self.close()
            raise WorkerUnavailable(f"策略子行程沒有起來：{ready}")

    def close(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        try:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            process.wait(timeout=2)
        except Exception:  # noqa: BLE001 -- 關不乾淨就殺掉，不要卡住呼叫端
            _abandoned.append(process)
            process.kill()
            # 殺過之後收屍失敗就算了：這個把手已經放掉那個行程，而呼叫端多半是盯
            # 盤迴圈——為了一具屍體卡住它，比留下一具屍體嚴重得多。
            with contextlib.suppress(Exception):
                process.wait(timeout=2)

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    # --- 溝通 -------------------------------------------------------------

    def _read_line(self, timeout: float) -> dict:
        """讀一行 JSON，逾時就把子行程殺掉。

        subprocess 的管線沒有 readline(timeout)，所以用一條丟棄式的執行緒去讀，主
        執行緒等它。逾時的時候殺掉子行程，那也會讓那條執行緒的 readline 結束——不
        然它會一直掛在那裡，而累積起來就是一個看不見的執行緒外洩。
        """
        process = self._process
        if process is None or process.stdout is None:
            raise WorkerUnavailable("策略子行程不在了")

        box: list[str] = []

        def _read() -> None:
            try:
                line = process.stdout.readline()  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                line = ""
            box.append(line)

        reader = threading.Thread(target=_read, daemon=True)
        reader.start()
        reader.join(timeout)
        if reader.is_alive():
            self._kill()
            raise StrategyTimedOut(f"策略在 {timeout} 秒內沒有回來，子行程已經殺掉")

        line = box[0] if box else ""
        if not line:
            self._kill()
            raise WorkerUnavailable("策略子行程死掉了")
        try:
            return json.loads(line)
        except json.JSONDecodeError as exc:
            # 半截的訊息不可以被當成「策略沒有訊號」。那會讓一次通訊故障看起來像一
            # 個正常的 hold，而使用者永遠不會知道那一輪其實沒有跑。
            self._kill()
            raise WorkerUnavailable(f"策略子行程回了讀不懂的東西：{line[:200]!r}") from exc

    def _kill(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        _abandoned.append(process)
        # 同上：殺不掉或收不到屍都不能讓呼叫端停下來。
        with contextlib.suppress(Exception):
            process.kill()
            process.wait(timeout=2)

    def request(self, command: str, timeout: float | None = None, **payload) -> dict:
        with self._lock:
            if not self.alive:
                raise WorkerUnavailable("策略子行程不在了")
            process = self._process
            # 不用 assert：`python -O` 會把 assert 整行拿掉，而這條管線的另一端跑
            # 的是使用者的程式碼。一個會被編譯器刪掉的檢查，等於沒有檢查。
            if process is None or process.stdin is None:
                raise WorkerUnavailable("策略子行程不在了")
            try:
                process.stdin.write(json.dumps({"cmd": command, **payload}) + "\n")
                process.stdin.flush()
            except Exception as exc:  # noqa: BLE001
                self._kill()
                raise WorkerUnavailable(f"送不出去：{exc}") from exc

            answer = self._read_line(timeout if timeout is not None else self._timeout)

        if answer.get("ok") is not True:
            raise StrategyWorkerError(answer.get("error") or "策略子行程沒有說原因")
        return answer.get("result") or {}

    # --- 指令 -------------------------------------------------------------

    def ping(self) -> bool:
        return self.request("ping").get("pong") is True

    def child_environment(self) -> list[str]:
        """子行程看得到的環境變數名稱。

        存在的理由只有一個：讓「那裡沒有秘密」這件事**驗得到**。一個宣稱有邊界但
        沒有辦法檢查的邊界，跟沒有邊界的差別只在文件上。
        """
        return list(self.request("env").get("names") or [])

    def compile(self, source_code: str, params: dict | None = None) -> dict:
        return self.request("compile", source_code=source_code, params=params or {})

    # --- 盯盤迴圈用的（#18 第 3 步）-----------------------------------------
    #
    # 每一支策略在子行程裡有一個 key。狀態（self.prices）活在那邊，這邊只留一個
    # 名字——那正是常駐子行程買到的東西：跨輪的記憶，而秘密不在那裡。

    def validate(self, source_code: str, prices: list[float], timeout: float) -> dict:
        return self.request("validate", timeout=timeout, source_code=source_code, prices=prices)

    def preload(self, timeout: float) -> None:
        """把沙箱先載進子行程。只有暖機用，跟任何一支策略無關。"""
        self.request("preload", timeout=timeout)

    def load(self, key: str, source_code: str, params: dict | None = None) -> dict:
        return self.request("load", key=key, source_code=source_code, params=params or {})

    def on_tick(self, key: str, price: float, timeout: float) -> str:
        return str(self.request("tick", timeout=timeout, key=key, price=price)["signal"])

    def on_bar(self, key: str, bar: dict, timeout: float) -> str:
        return str(self.request("bar", timeout=timeout, key=key, bar=bar)["signal"])

    def warm_up(self, key: str, bars: list[dict], timeout: float) -> None:
        self.request("warm", timeout=timeout, key=key, bars=bars)

    def drop(self, key: str) -> None:
        self.request("drop", key=key)
