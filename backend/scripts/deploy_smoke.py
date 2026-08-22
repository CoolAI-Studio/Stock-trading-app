"""Does a brand-new deployment actually come up? Asked of a running container.

WHY THIS EXISTS. Every test in this repo checks the CODE. Not one of them
checks the thing a new user actually does: click Deploy, fill in a form, open
the URL. Nobody has ever run that path end to end -- all the confidence in it
comes from reading render.yaml, which is not evidence.

So this asks the only question that matters for somebody's first five minutes,
and it asks it of a REAL container started the way Render starts it, with the
environment a person gets when they follow the README and leave the optional
boxes blank:

  1. Is the process alive at all?
     The image's CMD is `alembic upgrade head && uvicorn ...`, so a DATABASE_URL
     that is empty, malformed, or simply unreachable means uvicorn never runs
     and no port is ever bound. The setup page that exists to explain what is
     missing is then unreachable for exactly the reason it exists.

  2. Does the path the host probes answer 200, and does it say which build
     answered?
     render.yaml sets healthCheckPath, and a first deploy has no previous
     version to fall back to. A probe that never passes is a deploy the host
     marks as failed. The build identity rides along because the release gate
     in CI polls it: an out-of-date backend passes every other check here.

  3. Does /api/setup/status list what is missing, including the push keys?
     The audience for this app wants alerts on their phone. If the push key row
     is absent, the button that generates it never appears.

  4. Does the generate button actually produce a key?
     「Press this to make one」 is the whole reason a non-engineer can finish
     the form. It has to work with nothing configured.

WHAT IT DOES NOT COVER, said plainly: Render's own form, Neon's provisioning,
and Vercel's build. Those are three companies' user interfaces and only a
person clicking through can prove them. This covers everything from the
container's first instruction onwards.

Usage:
    python scripts/deploy_smoke.py http://localhost:8000
"""

import json
import sys
import urllib.error
import urllib.request

TIMEOUT_SEC = 15


def _get(url: str) -> tuple[int, dict | None]:
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SEC) as response:  # noqa: S310 # nosec B310
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001 -- the status code is what matters
            return exc.code, None
    except Exception as exc:  # noqa: BLE001
        print(f"    (no answer: {type(exc).__name__}: {exc})")
        return 0, None


def _post(url: str, payload: dict) -> tuple[int, dict | None]:
    request = urllib.request.Request(  # noqa: S310 # nosec B310
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:  # noqa: S310 # nosec B310
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return exc.code, None
    except Exception as exc:  # noqa: BLE001
        print(f"    (no answer: {type(exc).__name__}: {exc})")
        return 0, None


def run(base: str) -> list[str]:
    base = base.rstrip("/")
    problems: list[str] = []

    print("1. 行程活著嗎？")
    status, _ = _get(f"{base}/api/setup/status")
    if status == 0:
        problems.append(
            "容器沒有在服務任何東西。映像檔的 CMD 是 `alembic upgrade head && uvicorn`，"
            "所以資料庫連不上時 uvicorn 根本不會啟動，連埠都不會綁——而設定頁正是"
            "為了解釋「資料庫還沒設定好」而存在的。"
        )
        # Nothing else can be asked of a process that is not there.
        return problems
    print(f"   OK（/api/setup/status -> {status}）")

    print("2. 部署平台探測的那條路徑答 200 嗎？")
    status, health = _get(f"{base}/healthz")
    if status != 200:
        problems.append(
            f"/healthz 回 {status}，而 render.yaml 的 healthCheckPath 指著它。"
            "第一次部署沒有可以退回的舊版本，探測不過就是部署失敗——"
            "設定頁會在最需要它的時候服務不到。"
        )
    else:
        print("   OK")

    print("2b. 它說得出自己是哪一個 build 嗎？")
    # An old build passes every health check, because an old build is not a
    # sick one -- so 「did the deploy land?」 has no answer unless the probe
    # carries one. The CI deploy step polls exactly this field, which makes it
    # load-bearing: if the wiring breaks, the release gate silently starts
    # measuring nothing.
    #
    # The commit itself is allowed to be absent here: this container is
    # started the way a blank deploy starts it, with no build arg and no host
    # variable, and 「no commit」 is the honest answer to that. What must
    # always be there is the block and the start time.
    version = (health or {}).get("version")
    if not isinstance(version, dict) or not version.get("started_at"):
        problems.append(
            "/healthz 沒有說自己是哪一個 build。舊版的後端每一項健康檢查都會是綠的"
            "（舊版沒有生病，它只是舊的），所以沒有這一段，「部署到底有沒有成功」"
            "就沒有辦法從外面看出來——CI 的部署確認步驟讀的就是這個欄位。"
        )
    else:
        print(f"   OK（commit={version.get('commit')!r}，啟動於 {version['started_at']}）")

    print("3. /api/setup/status 說得出缺什麼嗎？")
    status, body = _get(f"{base}/api/setup/status")
    missing = [item.get("name") for item in (body or {}).get("missing", [])] if body else []
    if status != 200 or body is None:
        problems.append(f"/api/setup/status 回 {status}，新使用者看不到任何指引。")
    else:
        print(f"   缺少的項目：{missing or '（無）'}")
        if not any("VAPID" in str(key) for key in missing):
            problems.append(
                "推播金鑰不在缺少清單裡，所以設定頁不會出現那一列，"
                "「產生推播金鑰」的按鈕永遠不會出現。這個 app 的使用者要的就是"
                "手機通知。"
            )

    print("4. 產生按鈕真的生得出金鑰嗎？")
    status, body = _post(f"{base}/api/setup/generate", {"kind": "vapid"})
    if status not in (200, 201) or not body:
        problems.append(
            f"/api/setup/generate 回 {status}。「按這裡產生一組」是這個表單"
            "唯一能被非工程師填完的原因。"
        )
    else:
        print("   OK")

    return problems


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("用法：python scripts/deploy_smoke.py <網址>")
        return 2

    print("以一份「所有選填都留白」的全新部署來檢查：\n")
    problems = run(argv[1])

    if not problems:
        print("\n全部通過——一份空白設定的部署起得來，而且說得出還缺什麼。")
        return 0

    print(f"\n新使用者會在這 {len(problems)} 個地方卡住：\n")
    for problem in problems:
        print(f"  - {problem}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
