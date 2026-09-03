"""Check from outside that the alerts are still running.

/healthz already knows everything worth knowing -- it runs a real query, reads
the worker's heartbeat, counts empty market-data polls, and answers 503 when
any of it is wrong. Its docstring has said "UptimeRobot hits it every 5
minutes" since it was written. Nothing ever hit it.

So the failure this product cannot survive -- the worker stops, and every
alert stops with it -- was detectable only by a human opening the page. That
is exactly the person who is not looking: they set an alert so they would not
have to.

A dying process cannot report its own death, which is why this runs somewhere
else: a scheduled GitHub Actions job (the repo is public, so the minutes cost
nothing). A failed scheduled workflow emails the repo owner, and that email is
the whole point of the file.

NO SECOND DEFINITION OF HEALTHY. This never re-derives whether the worker is
late; /healthz owns that (settings.HEALTH_MAX_AGE_SEC, the startup grace
window, the empty-poll run). Two definitions would drift, and the one that
drifted would be the one that stopped mailing. All this adds is "did anything
answer at all", which is the one question the service cannot answer about
itself.

    python scripts/watchdog.py https://your-app.onrender.com/healthz
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

# How long to wait before looking again after no answer. Render's free tier
# spins the process down when idle and a cold start takes the better part of a
# minute -- during which the service is genuinely unreachable and genuinely
# fine. Shorter than this and the retry is decoration: both requests land
# inside the same cold start, both fail, and the owner gets an email about an
# outage that was a boot.
COLD_START_WAIT_SEC = 45

# Long enough to sit through a cold start rather than time out in the middle of
# one and call it an outage.
TIMEOUT_SEC = 60

# What each check guards, in the owner's language. The email has to say what
# has actually stopped working; "health check failed" sends them to a
# dashboard to work it out from scratch at whatever hour it arrived.
_MEANING = {
    "worker": "背景 worker 沒有在跑 —— 策略不會執行，提醒不會發出。",
    "market_data": "抓不到行情 —— 沒有價格就不會有任何提醒。",
    "database": "連不上資料庫 —— 策略、持倉、通知設定全都讀不到。",
    "notifications": "通知功能被關掉了（NOTIFICATIONS_ENABLED）—— 策略照跑，但一則警告都不會送出。",
    "symbols": "有代號一直抓不到報價 —— 這些代號上面的提醒等於停擺，其他代號不受影響。",
    "strategies": (
        "策略叫不動它的子行程 —— 行情抓得到、迴圈也在轉，但那些策略一支都沒跑，"
        "所以一則提醒都不會發出。多半是記憶體不夠或容器剛重啟；重新部署一次通常就好了。"
    ),
    "bars": (
        "抓不到 K 棒 —— 報價回得來、迴圈也在轉，但看 K 線的那幾支策略一支都沒跑，"
        "所以它們一則提醒都不會發出。報價和 K 棒走的是上游不同的端點，所以可能只壞一半。"
    ),
    "setup": (
        "這個部署還沒設定完成 —— API 是鎖住的，沒有任何功能在跑。"
        "打開你的前端網址，照設定頁的指示把缺的欄位填完。"
    ),
}


def read_verdict(status_code: int | None, body: str | None) -> list[str]:
    """What is wrong, in the owner's language. Empty means healthy.

    `status_code is None` means nothing answered at all, which covers the worst
    outcome there is -- the whole deployment gone -- so it can never be silent.
    """
    if status_code is None:
        return ["連不上服務（完全沒有回應）。整個後端可能已經停掉，所有提醒都不會發出。"]

    try:
        payload = json.loads(body or "")
    except (json.JSONDecodeError, TypeError):
        # A proxy error page, a parked domain, somebody else's app on that
        # hostname: all answer with something that is not this service.
        # Treating "it responded" as "it is healthy" would report green for a
        # deployment that no longer exists.
        return [
            f"服務有回應（HTTP {status_code}），但回傳的內容看不懂，"
            "不是這個 app 的健康檢查格式。這個網址現在指向的可能不是你的後端。"
        ]

    # A deployment that is UP but never finished setup sends no alerts at all,
    # and that is exactly the silence this watchdog exists to break. It used to
    # be caught for free, because /healthz answered 503 while setup was
    # incomplete -- but render.yaml probes the same URL, and a first deploy
    # whose probe never passes is a deploy Render marks as failed. So the probe
    # answers 200 now and says 「setup」 in the body, and the verdict has to
    # read the body rather than the code.
    if isinstance(payload, dict) and payload.get("status") == "setup":
        return [
            "這個部署還沒完成設定，所以它不會發出任何提醒。"
            "打開前端頁面，照設定頁上的指示把缺少的值填完。"
        ]

    checks = payload.get("checks") if isinstance(payload, dict) else None
    if not isinstance(checks, dict):
        return [
            f"服務有回應（HTTP {status_code}），但回傳的 JSON 裡沒有 checks 區塊，"
            "不是這個 app 的健康檢查格式。"
        ]

    problems = []
    for name, check in checks.items():
        # Anything that is not an outright "fail" is left alone on purpose --
        # 'starting' is the grace window after a cold start, and 'disabled' is
        # a deliberate configuration. The endpoint already drew those lines.
        if not isinstance(check, dict) or check.get("status") != "fail":
            continue
        sentence = _MEANING.get(name, f"{name} 檢查失敗。")
        # The symbols check names what is broken, and the whole point of this
        # email is that the owner should not have to open a dashboard at 3am
        # to find out which of their rows it was. Read defensively: an older
        # deployment answering a newer watchdog must still produce a report.
        stale = check.get("stale_symbols")
        if isinstance(stale, list) and stale:
            sentence += "：" + "、".join(str(symbol) for symbol in stale)
        problems.append(sentence + f"（{name}）")

    if not problems and status_code != 200:
        # It said nothing is wrong and still refused. Worth reporting rather
        # than trusting either half of a contradiction.
        problems.append(
            f"HTTP {status_code}，但回傳的檢查項目都說正常。"
            "可能是 Render 或中間的代理擋下來的，不是 app 自己回的。"
        )

    return problems


# urlopen speaks more than http. `file:///etc/passwd` opens a local file and
# `ftp://` opens a socket, and the address this script is pointed at comes from
# outside it -- a GitHub repo variable (HEALTH_URL) on the scheduled run, argv
# on a manual one.
#
# Nothing here is exploitable today: that variable is set by the repo owner,
# who already has far more direct ways to run code in their own Actions job.
# The reason to close it anyway is that this is the ONE thing keeping watch
# when nobody is looking, and 「the watchdog opened a local file and reported
# healthy」 is a failure with no other detector behind it.
#
# Plain http stays allowed: a self-hosted copy on a LAN has no certificate, and
# no watchdog at all is worse than one on a plaintext connection to an endpoint
# that carries no secrets.
_ALLOWED_SCHEMES = ("http", "https")


def fetch(url: str) -> tuple[int | None, str | None]:
    """The response, or (None, None) if nothing came back at all."""
    if urllib.parse.urlparse(url).scheme not in _ALLOWED_SCHEMES:
        # Not 「guess a scheme and carry on」: prepending https:// to whatever
        # was typed is the kind of helpfulness that silently points the
        # watchdog somewhere else and then reports that place healthy.
        return None, None
    try:
        # The scheme check at the top of this function is the audit this
        # warns to do. bandit is static and cannot see a guard three lines
        # up, so the annotation records that it was done rather than
        # waving the finding away.
        with urllib.request.urlopen(  # noqa: S310  # nosec B310
            url, timeout=TIMEOUT_SEC
        ) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        # A 503 is an ANSWER, and the most informative one there is -- the body
        # carries which check failed. Losing it to an exception handler would
        # turn every real outage into a generic "could not connect".
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except Exception:
        return None, None


def run_check(
    fetch_once: Callable[[], tuple[int | None, str | None]],
    *,
    attempts: int = 2,
    wait: Callable[[float], None] = time.sleep,
) -> list[str]:
    """Ask, and ask again only when the answer was "nothing".

    A 503 with a dead worker is a definite answer; retrying it only delays the
    email. No answer at all is the ambiguous case -- on a free tier it is
    usually a cold start -- so that one, and only that one, is worth a second
    look.
    """
    problems: list[str] = []
    for attempt in range(attempts):
        status_code, body = fetch_once()
        problems = read_verdict(status_code, body)
        if not problems:
            return []
        if status_code is not None:
            # It answered. Asking again tells us nothing new.
            return problems
        if attempt < attempts - 1:
            wait(COLD_START_WAIT_SEC)
    return problems


def main(argv: list[str]) -> int:
    # Every message here is Traditional Chinese, and Python picks the console's
    # own codepage for stdout -- cp950 on the owner's Windows machine, which
    # mangles the text into unreadable bytes. The runner it normally runs on is
    # UTF-8 so this would only ever break when read locally, which is exactly
    # when someone is debugging and needs to read it.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    if len(argv) < 2 or not argv[1].strip():
        print("用法：python scripts/watchdog.py <健康檢查網址>")
        return 2

    url = argv[1].strip()
    problems = run_check(lambda: fetch(url))

    if not problems:
        # THE URL IS NOT PRINTED. This runs as a GitHub Actions schedule every
        # 15 minutes and the repository is PUBLIC, so every line here is readable
        # by anyone -- printing it published the owner's deployment address to the
        # internet 96 times a day. It is not a credential, but it is the address
        # somebody needs before they can try anything at all against it, and the
        # line bought nothing: whoever set HEALTH_URL has exactly one backend and
        # already knows where it is.
        print("OK — 後端回報一切正常。")
        return 0

    print("後端有問題（網址見 repository variable HEALTH_URL）：\n")
    for problem in problems:
        print(f"  - {problem}")
    print(
        "\n這代表提醒可能已經停擺。先去 Render 的 dashboard 看 log，"
        "確認服務有沒有在跑；必要時按一次 Manual Deploy。"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
