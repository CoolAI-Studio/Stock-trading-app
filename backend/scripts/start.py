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


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    problem = run_migrations()
    if problem:
        # ERROR, not a crash. The next line starts the server that will explain
        # this to whoever is looking at the setup page.
        logger.error("資料庫遷移沒有跑成功，服務仍然會啟動，好讓設定頁說得出原因：\n%s", problem)
        # 讓 app 那一側也讀得到，設定頁才分得出「還沒填」和「填了連不上」。
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
