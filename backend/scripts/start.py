"""容器的啟動流程：先跑遷移，**跑不動也照樣把服務起起來**。

WHY THIS FILE EXISTS. 原本的啟動指令是

    alembic upgrade head && uvicorn app.main:app ...

`&&` 的意思是：連不上資料庫 → alembic 非零退出 → uvicorn 從來沒有被執行 → 沒有
任何一個埠被綁起來。使用者拿到的是一個死掉的網址。

而 `DATABASE_URL` 正是部署表單上唯一一格「app 生不出來、只能自己去別人家的服務
複製貼上」的值，也就是最可能貼錯的那一格。這個 app 有一整套設定模式，準備好在
那種時候告訴他缺什麼、哪裡填——而那段話只有在行程活著的時候送得出去。

所以順序反過來：遷移失敗是一個要被**回報**的狀況，不是一個讓行程結束的理由。
服務照起，設定頁照答，`/healthz` 照回 200 並在 body 裡說自己在設定模式。

一個正常的部署完全不受影響：遷移照樣在開機時跑完，這仍然是 schema 到達最新的
唯一時機。
"""

import logging
import os
import re
import subprocess  # nosec B404
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

logger = logging.getLogger("app.start")

# 連線字串裡的帳號密碼。驅動的錯誤訊息會把整串 DSN 原封不動印出來，而這些訊息
# 會進 log、也會被設定頁讀出來顯示在畫面上。
_CREDENTIALS = re.compile(r"(?P<scheme>[a-zA-Z0-9+]+://)[^/\s@]+@")


def scrub(text: str) -> str:
    """把任何 `scheme://user:password@` 換成 `scheme://***@`。"""
    return _CREDENTIALS.sub(lambda m: f"{m.group('scheme')}***@", text)


def run_migrations() -> str | None:
    """把 schema 帶到最新。回傳 None 代表成功，否則回傳一句能顯示的理由。

    Never raises, and never exits: the whole point is that the server starts
    either way. The reason is scrubbed because it routinely contains the DSN.
    """
    try:
        completed = subprocess.run(  # nosec B603 B607
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT,
            capture_output=True,
            text=True,
            # **一定要指定編碼。** text=True 在 Windows 上會用系統的字碼頁（這台
            # 開發機是 cp950）去解，而子行程寫的是 UTF-8——那會在讀取的執行緒裡
            # 丟 UnicodeDecodeError，而它是在**另一條執行緒**上炸的，所以呼叫端
            # 只拿得到一段空輸出。
            #
            # 這裡捕捉的是要顯示給人看的失敗原因，所以解錯碼會讓一次成功的遷移被
            # 報成失敗——而這個檔案存在的全部理由就是「起得來而且說得出原因」。
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
    except Exception as exc:  # noqa: BLE001 -- 任何失敗都只是「遷移沒跑成」
        return scrub(f"{type(exc).__name__}: {exc}")

    if completed.returncode == 0:
        return None

    output = (completed.stderr or completed.stdout or "").strip()
    return scrub(readable_reason(output) or f"alembic exited with {completed.returncode}")


def readable_reason(output: str) -> str:
    """A failure said in one line, or as close to it as the output allows.

    THE SETUP PAGE PRINTS THIS VERBATIM, under 「對方回的是：」, to somebody who
    is not a programmer. It used to be the last eight lines of alembic's
    stderr, which for the commonest failure is the tail of a Python traceback:
    file paths on this machine, sqlalchemy's own filenames, and a caret line.
    None of that says what is wrong. The LAST line does, and it is the only
    line that ever did.

    Everything the traceback machinery adds is dropped -- the header, the
    frame lines, the source echoed under each frame, the carets. What is left
    is the exception itself; output that was never a traceback in the first
    place (alembic says plenty of things in plain sentences) comes through
    untouched.
    """
    kept: list[str] = []
    skip_source_echo = False
    for raw in output.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.strip() == "Traceback (most recent call last):":
            continue
        if line.lstrip().startswith('File "'):
            # The line after a frame is the source it points at, indented.
            skip_source_echo = True
            continue
        if skip_source_echo and raw[:1].isspace():
            continue
        skip_source_echo = False
        kept.append(line)

    # The last two, not just the last: a chained failure ends with a cause
    # line or a 「(Background on this error at: ...)」, and the useful half is
    # not always the final one.
    return "\n".join(kept[-2:]).strip()


# 探測資料庫的預算。要小：它擋在服務起來的前面，而它的答案只決定要不要鎖住設定頁。
_PROBE_CONNECT_TIMEOUT_SEC = 5
# **查詢也要有期限，不只連線。** libpq 的 connect_timeout 只管連線建立，不管查詢跑多
# 久——一個連得上但卡在鎖上的資料庫會讓這裡永遠回不來，而「回不來」正是這個檔案存在要
# 防的那件事：一個死掉的網址。
_PROBE_STATEMENT_TIMEOUT_MS = 3000
# 問幾次。「容器起來那一刻資料庫剛好抖一下」是這條路上真的會遇到的一種：alembic 幾秒內
# 就連不上而失敗，十秒後資料庫卻回來了。只問一次會把那種情況判成「第一次部署」，而那個
# 判斷的代價是他所有的提醒停擺。
_PROBE_ATTEMPTS = 3
_PROBE_GAP_SEC = 2


def deployment_has_accounts() -> bool:
    """這個資料庫裡已經有帳號了嗎。問不到就是 False。

    這是「這份部署以前成功跑起來過」唯一問得到的證據，而它決定一次跑不動的遷移是什麼
    意思：

        沒有帳號（或問不到）→ 這份部署從來沒有跑起來過 → 設定模式，設定頁解釋
        已經有帳號          → 是我們這次的更新弄壞了什麼 → **不可以**鎖

    走錯第二邊的代價不對稱，而且正是 CLAUDE.md「更新不可以停掉已經在跑的那一份」整條在
    講的形狀：鎖住的話 app/main.py 的 run_worker 是 False，worker 一次都不跑，他每一則
    提醒都不會送出去，而且行程活著就不會自己復原——畫面上只寫「這個部署還沒設定完成」，
    而他昨天什麼都沒動。

    用「有沒有帳號」而不是「schema 在不在」，因為它同時就是「有沒有東西可以失去」：沒有
    帳號就沒有策略、沒有提醒，鎖住不會讓任何一則通知消失。

    問不到算沒有，因為連不上正是第一次部署最常見的樣子（DATABASE_URL 是整張表單上唯一
    一個 app 生不出來、只能去別人家的服務複製貼上的值），而那時候鎖住是對的。

    **不丟例外，也不會永遠回不來。** 為了問這個問題把服務卡在開機是本末倒置。
    """
    for attempt in range(_PROBE_ATTEMPTS):
        try:
            from sqlalchemy import create_engine, select, text

            from app.config import settings
            from app.models.user import User

            url = settings.DATABASE_URL
            connect_args: dict = {}
            if url.startswith("postgres"):
                connect_args["connect_timeout"] = _PROBE_CONNECT_TIMEOUT_SEC
            engine = create_engine(url, connect_args=connect_args, pool_pre_ping=False)
            try:
                with engine.connect() as connection:
                    if url.startswith("postgres"):
                        # 連得上但卡在鎖上，是這條路真的會遇到的一種——而它跟連不上的
                        # 差別在於：連不上會很快回來，卡住不會。
                        connection.execute(
                            text(f"SET statement_timeout = {_PROBE_STATEMENT_TIMEOUT_MS}")
                        )
                    found = connection.execute(select(User.__table__.c.id).limit(1)).first()
                return found is not None
            finally:
                engine.dispose()
        except Exception as exc:  # noqa: BLE001 -- 問不到就是沒有，不是壞掉
            logger.info("問不到這個部署有沒有帳號（第 %s 次）：%s", attempt + 1, exc)
            if attempt + 1 < _PROBE_ATTEMPTS:
                time.sleep(_PROBE_GAP_SEC)
    return False


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    problem = run_migrations()
    if problem:
        # ERROR, not a crash. The next line starts the server that will explain
        # this to whoever is looking at the setup page.
        logger.error("資料庫遷移沒有跑成功，服務仍然會啟動，好讓設定頁說得出原因：\n%s", problem)
        # 讓 app 那一側也讀得到，設定頁才分得出「還沒填」和「填了連不上」。
        # **已經有帳號的部署不可以被鎖起來。**
        #
        # DATABASE_MIGRATION_ERROR 會讓 app 進設定模式，而設定模式下 run_worker 是
        # False——一次跑不動的遷移就讓一份跑了三個月的部署所有提醒停擺，而且行程活
        # 著就不會自己復原。這正是 #50 那條規則的形狀，只是入口從編譯移到開機。
        #
        # 「不鎖」不等於「不說」：理由留在 WARNING 裡，log 也照樣印。
        if deployment_has_accounts():
            logger.error(
                "這個部署已經有帳號了，所以不會因為這次遷移失敗而鎖住——worker 照跑、"
                "提醒照送。但 schema 可能跟程式碼對不上，請看上面的原因。"
            )
            os.environ["DATABASE_MIGRATION_STALE"] = problem
        else:
            os.environ["DATABASE_MIGRATION_ERROR"] = problem
    else:
        logger.info("資料庫遷移完成")

    port = os.environ.get("PORT", "8000")
    argv = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "0.0.0.0",  # nosec B104 -- 容器裡就是要對外綁
        "--port",
        port,
        # 背景盯盤的迴圈和 WS 票券都是行程內的單例：開兩份會把每一個訊號通知兩次。
        "--workers",
        "1",
    ]

    if os.name == "nt":
        # Windows 沒有 exec 的語意，而且它的 os.exec* 是用空白把參數接起來、
        # 不加引號的——直譯器路徑裡只要有一個空白（"…\Stock trading app\…"）
        # 整條指令就被切斷。開發機上跑得到，才有人會在推之前試這條路。
        return subprocess.run(argv).returncode  # nosec B603

    # 容器裡用 exec：PID 1 應該是 uvicorn 自己，否則平台送來的停止訊號會停在這支
    # 腳本上，而它不知道要怎麼把還在處理的請求收乾淨。
    os.execv(sys.executable, argv)  # nosec B606
    return 0


if __name__ == "__main__":
    sys.exit(main())
