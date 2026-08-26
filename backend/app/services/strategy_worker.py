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

import json
import os
import subprocess
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

    刻意不細分成「策略寫壞了」和「worker 死了」兩種：呼叫端要做的事是一樣的——把
    它記在那一支策略上，然後繼續跑下一支。分得太細只會讓呼叫端多兩個 except，而
    每一個 except 都是一個可能忘記寫的地方。
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
        self._process = subprocess.Popen(
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
            raise StrategyWorkerError(f"策略子行程沒有起來：{ready}")

    def close(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        try:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            process.wait(timeout=2)
        except Exception:  # noqa: BLE001 -- 關不乾淨就殺掉，不要卡住呼叫端
            process.kill()
            try:
                process.wait(timeout=2)
            except Exception:  # noqa: BLE001
                pass

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
            raise StrategyWorkerError("策略子行程不在了")

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
            raise StrategyWorkerError(f"策略子行程在 {timeout} 秒內沒有回應，已經殺掉")

        line = box[0] if box else ""
        if not line:
            self._kill()
            raise StrategyWorkerError("策略子行程死掉了")
        try:
            return json.loads(line)
        except json.JSONDecodeError as exc:
            # 半截的訊息不可以被當成「策略沒有訊號」。那會讓一次通訊故障看起來像一
            # 個正常的 hold，而使用者永遠不會知道那一輪其實沒有跑。
            self._kill()
            raise StrategyWorkerError(f"策略子行程回了讀不懂的東西：{line[:200]!r}") from exc

    def _kill(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            process.kill()
            process.wait(timeout=2)
        except Exception:  # noqa: BLE001
            pass

    def request(self, command: str, timeout: float | None = None, **payload) -> dict:
        with self._lock:
            if not self.alive:
                raise StrategyWorkerError("策略子行程不在了")
            process = self._process
            assert process is not None and process.stdin is not None
            try:
                process.stdin.write(json.dumps({"cmd": command, **payload}) + "\n")
                process.stdin.flush()
            except Exception as exc:  # noqa: BLE001
                self._kill()
                raise StrategyWorkerError(f"送不出去：{exc}") from exc

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
