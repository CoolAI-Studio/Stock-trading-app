"""排練一次升級：上一個 released 版本 → 這一個 commit，資料要活著。

＊ 為什麼需要這支腳本。

這個 repo 每一次 push，都是別人機器上的一次升級（CLAUDE.md #50、#52：後端追 `stable`
＋ autoDeploy）。而在它出現之前，CI 驗的是「**從空的**建起來」——空資料庫、SQLite、第
一次啟動。

真實事件是另一件：他的資料庫裡已經有東西，而且是 Postgres。那條路上有兩件從來沒跑過
的事：帶 dialect 守衛的遷移在 SQLite 上整支被跳過（所以那些手寫的 `ALTER TABLE` 語法
沒有人執行過），以及「既有的列」——一支忘了給預設值的遷移在空資料庫上永遠是綠的。

＊ 而「服務起得來」**不等於**「遷移跑成了」。

容器的 CMD 是 `python scripts/start.py`，而那支腳本刻意不用
`alembic upgrade head && uvicorn`：遷移跑不動的時候服務照樣起來，好讓設定頁說得出原因
（一個死掉的網址送不出任何說明）。已經有帳號的部署更是刻意**不鎖**——一次跑不動的遷移
不該讓一份跑了三個月的部署所有提醒停擺（#50 的形狀，入口從編譯移到開機）。

那個設計是對的，但它讓「curl /healthz 有回答」在這一關裡幾乎沒有意義：一支壞掉的遷移照
樣會讓那一步變綠。所以這裡要問的是**系統狀態頁**——`start.py` 把原因放進
`DATABASE_MIGRATION_STALE`，而 `/api/system/status` 的 database 那一格會因此變成 warn
並把原因原樣帶出來。

＊ 兩半。

    seed    在**舊版**上建出一個真的使用者會有的東西：帳號、自選、通知管道（設定是加
            密存的）、一支啟用中的策略
    verify  換成**新版**、指同一個資料庫之後，那些東西還在不在

只有前一半的話，這一關證明的只是「新版起得來」；而升級真正會弄丟的是資料。

＊ 為什麼挑這四樣。

    帳號        登得進去才有一切；密碼雜湊的欄位動過就會全部鎖在外面
    通知管道    設定是 EncryptedJSON 存的。列得出來就代表**解得開**，而
                config_preview 裡的 chat_id 是明文回來的那一半——「舊資料在新版讀得回
                來」只有這一格證明得了
    策略        CLAUDE.md #50 的原話是「更新不可以停掉已經在跑的那一份」，而最糟的形狀
                就是升級之後策略被停用：畫面上只寫「停用」，沒有東西說為什麼
    自選        最普通的一列資料。它不見了，代表遷移把使用者的資料表整個換掉了

用法：

    python scripts/upgrade_smoke.py seed   http://localhost:8000 --out seed.json
    python scripts/upgrade_smoke.py verify http://localhost:8000 --seed seed.json
"""

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT_SEC = 30

# 這一份排練用的固定值。密碼只活在這個容器的生命週期裡，而它必須在兩次啟動之間一樣
# ——「同一個人還登得進去」正是要驗的東西。
OWNER_EMAIL = "upgrade-rehearsal@example.com"
OWNER_PASSWORD = "correct-horse-battery-staple"  # noqa: S105 # nosec B105
WATCHED_SYMBOL = "2330.TW"
CHANNEL_LABEL = "升級排練用的管道"
CHAT_ID = "rehearsal-chat-id"
# 假值，只是為了讓那一格通得過驗證——這支腳本從來不會真的送出任何訊息（管道建好之後
# 沒有人按過測試）。它存在的意義是「有一列加密的設定要在升級之後讀得回來」。
BOT_TOKEN = "rehearsal-bot-token"  # noqa: S105 # nosec B105
STRATEGY_NAME = "升級排練用的策略"

# 最小但真的編得過的一支。編譯就是執行（CLAUDE.md #18），所以這一段會在子行程裡真的
# 被跑起來——它同時驗到「新版的沙箱還載得動舊版存下來的原始碼」。
STRATEGY_SOURCE = "\n".join(
    [
        "class Strategy:",
        '    """升級排練用。不產生任何訊號，只要能被載入就夠了。"""',
        "",
        "    def __init__(self):",
        '        self.name = "升級排練用的策略"',
        '        self.symbol = "2330.TW"',
        "        self.warmup_bars = 1",
        '        self.timeframe = "1d"',
        "",
        "    def on_bar(self, bar) -> str:",
        '        return ""',
        "",
    ]
)


class Failed(Exception):
    """排練失敗。訊息會直接變成 CI 上那一行紅字，所以要寫成看得懂的話。"""


def _request(method: str, url: str, *, token: str | None = None, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)  # noqa: S310 # nosec B310
    if body is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:  # noqa: S310 # nosec B310
            raw = response.read().decode("utf-8")
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except Exception:  # noqa: BLE001 -- 狀態碼和原文才是要看的
            return exc.code, {"raw": raw}
    except Exception as exc:  # noqa: BLE001
        raise Failed(f"{method} {url} 連不上：{exc}") from exc


def _expect(status_code: int, allowed: tuple[int, ...], what: str, payload) -> None:
    if status_code not in allowed:
        raise Failed(f"{what}：預期 {allowed}，拿到 {status_code} —— {payload}")


def _login(base_url: str) -> str:
    """表單編碼，不是 JSON：這支端點用的是 OAuth2PasswordRequestForm。"""
    form = urllib.parse.urlencode({"username": OWNER_EMAIL, "password": OWNER_PASSWORD})
    request = urllib.request.Request(  # noqa: S310 # nosec B310
        f"{base_url}/api/auth/login",
        data=form.encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:  # noqa: S310 # nosec B310
            return json.loads(response.read().decode("utf-8"))["access_token"]
    except Exception as exc:  # noqa: BLE001
        raise Failed(f"登入失敗——升級之後他被鎖在自己的部署外面了：{exc}") from exc


def seed(base_url: str) -> dict:
    """在舊版上建出一個真的使用者會有的東西。"""
    status_code, payload = _request(
        "POST",
        f"{base_url}/api/auth/register",
        body={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
    )
    _expect(status_code, (201,), "建立第一個帳號", payload)

    token = _login(base_url)

    status_code, payload = _request(
        "POST", f"{base_url}/api/watchlist", token=token, body={"symbol": WATCHED_SYMBOL}
    )
    _expect(status_code, (200, 201), "加一筆自選", payload)

    status_code, channel = _request(
        "POST",
        f"{base_url}/api/notifications/channels",
        token=token,
        body={
            "channel_type": "telegram",
            "label": CHANNEL_LABEL,
            "config": {"bot_token": BOT_TOKEN, "chat_id": CHAT_ID},
        },
    )
    _expect(status_code, (201,), "建一個通知管道", channel)

    status_code, strategy = _request(
        "POST",
        f"{base_url}/api/strategies",
        token=token,
        body={
            "name": STRATEGY_NAME,
            "symbol": WATCHED_SYMBOL,
            "source_code": STRATEGY_SOURCE,
            "warmup_bars": 1,
        },
    )
    _expect(status_code, (201,), "建一支策略", strategy)

    # **啟用它。** 新建的策略預設是停用的，而這一關要驗的正是「升級之後啟用中的那一支
    # 還是啟用中」（CLAUDE.md #50）。種一支停用的下去，那條斷言就永遠是真的。
    status_code, strategy = _request(
        "POST", f"{base_url}/api/strategies/{strategy['id']}/activate", token=token
    )
    _expect(status_code, (200,), "啟用那支策略", strategy)
    if not strategy.get("is_active"):
        raise Failed("策略啟用不起來，這一關接下來驗的東西就沒有意義了")

    return {
        "channel_id": channel["id"],
        "strategy_id": strategy["id"],
        "strategy_active": strategy.get("is_active"),
        "from_version": _version(base_url),
    }


def _version(base_url: str) -> str | None:
    _, payload = _request("GET", f"{base_url}/healthz")
    return ((payload or {}).get("version") or {}).get("commit")


def migration_problem(status_payload: dict) -> str | None:
    """狀態頁說遷移有沒有跑成。沒問題回 None，有問題回那句可以直接顯示的原因。

    純函式，這樣「這一關到底看不看得出壞掉的遷移」測得到，不用真的弄壞一支遷移。

    判準是 database 那一格不是 ok 就算有問題。在這一關的情境下（真的 Postgres、剛升級
    完）那一格只有兩種變成非 ok 的理由，而**兩種都該讓這一關紅**：遷移沒跑完，或者資
    料庫其實是容器裡的一個檔案（那代表 DATABASE_URL 根本沒接上這個 Postgres，這一關就
    什麼都沒驗到）。
    """
    database = (status_payload or {}).get("database") or {}
    if database.get("status") == "ok":
        return None
    return database.get("detail") or f"database 那一格是 {database.get('status')!r}"


def verify(base_url: str, seeded: dict) -> None:
    """換成新版之後，那些東西還要在。"""
    now = _version(base_url)
    if now and seeded.get("from_version") and now == seeded["from_version"]:
        # 不是失敗：`stable` 剛好就是這個 commit 的時候（例如重跑一次 CI）本來就會一
        # 樣。說出來，免得有人以為這一關驗過了它其實沒驗到的東西。
        #
        # **這一句只在映像檔帶得動版本號的時候才有用。** CI 建的映像檔沒有
        # APP_GIT_COMMIT（那是環境變數，不是 build arg——見 CLAUDE.md #53），所以那裡
        # 兩邊都是 None，這個 if 進不來。真正管用的是工作流程那一側：它比對的是
        # `stable` 的 SHA 和 github.sha，那兩個一定拿得到。
        print(f"注意：兩次跑的是同一版（{now}），這一關這次沒有真的跨版本。")

    token = _login(base_url)

    status_code, watchlist = _request("GET", f"{base_url}/api/watchlist", token=token)
    _expect(status_code, (200,), "讀自選", watchlist)
    if WATCHED_SYMBOL not in [item["symbol"] for item in watchlist]:
        raise Failed(f"升級之後自選裡的 {WATCHED_SYMBOL} 不見了")

    status_code, channels = _request("GET", f"{base_url}/api/notifications/channels", token=token)
    _expect(status_code, (200,), "讀通知管道（列得出來就代表加密設定解得開）", channels)
    mine = [c for c in channels if c["id"] == seeded["channel_id"]]
    if not mine:
        raise Failed("升級之後通知管道不見了——他不會再收到任何提醒，而畫面上不會說")
    preview = mine[0].get("config_preview") or ""
    if CHAT_ID not in preview:
        raise Failed(
            f"通知管道的加密設定讀不回來（config_preview={preview!r}）。"
            "金鑰或 EncryptedJSON 的格式在這一版變了，舊的管道全部失效。"
        )

    status_code, strategies = _request("GET", f"{base_url}/api/strategies", token=token)
    _expect(status_code, (200,), "讀策略", strategies)
    mine = [s for s in strategies if s["id"] == seeded["strategy_id"]]
    if not mine:
        raise Failed("升級之後策略不見了")
    if seeded.get("strategy_active") and not mine[0].get("is_active"):
        raise Failed(
            "升級之後策略被停用了。CLAUDE.md #50：更新不可以停掉已經在跑的那一份——"
            "而畫面上只會寫『停用』，沒有東西說為什麼。"
        )

    # **這一段才是「遷移真的跑成了」的證據。** 上面每一項都可能在一個 schema 沒跟上的
    # 部署上照樣通過——那些端點只是剛好沒碰到新的那一欄。
    status_code, system = _request("GET", f"{base_url}/api/system/status", token=token)
    _expect(status_code, (200,), "讀系統狀態頁", system)
    problem = migration_problem(system)
    if problem:
        raise Failed(f"升級之後 schema 跟程式碼對不上：{problem}")

    status_code, health = _request("GET", f"{base_url}/healthz?deep=1")
    database = ((health or {}).get("checks") or {}).get("database", {}).get("status")
    if database not in {"ok", "skipped"}:
        raise Failed(f"升級之後健康檢查的資料庫那一格是 {database}：{health}")

    print("升級排練通過：遷移跑完了，帳號、自選、加密的通知設定、啟用中的策略都活著。")


def main() -> int:
    parser = argparse.ArgumentParser(description="排練一次升級")
    parser.add_argument("mode", choices=("seed", "verify"))
    parser.add_argument("base_url")
    parser.add_argument("--out", help="seed：把建了什麼寫到這個檔案")
    parser.add_argument("--seed", help="verify：讀那個檔案")
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")

    try:
        if args.mode == "seed":
            created = seed(base_url)
            if args.out:
                with open(args.out, "w", encoding="utf-8") as handle:
                    json.dump(created, handle)
            print(f"種好了：{created}")
        else:
            with open(args.seed, encoding="utf-8") as handle:
                verify(base_url, json.load(handle))
    except Failed as exc:
        print(f"::error::升級排練失敗：{exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
