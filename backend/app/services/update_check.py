"""他這一份是不是舊的。

＊ 為什麼要有這個。

使用者的副本是從我們的 repo 部署的，而我們每一次改動都是他機器上的一次更新——他不
在場、沒有 CI、也不知道我們改了什麼。#50 和 #51 修掉的兩個洞就是例子：一個在更新時
會停掉他每一支策略，一個會停掉整個 app。

`stable` ＋ autoDeploy 解決的是「之後才部署的人」。關掉自動更新的人、以及前端那半
（Vercel 的 clone 會複製一份 repo，來源就斷了）都還在外面。**看得見**是那些人唯一
的路。

＊ 「不知道」是一個答案，而且要說出口。

抓不到 GitHub（他的機器連不出去、GitHub 掛了、被限流），或者這個平台不告訴容器它建
的是哪一個 commit——那些時候唯一誠實的答案是不知道：

    說成「已經是最新」  → 他錯過安全修補
    說成「有新版」      → 他為了一個不存在的更新去重新部署，而那有它自己的風險

這跟 build_info.commit() 的做法是同一條：**None，永遠不要編一個出來。**

＊ 它絕對不可以影響到提醒。

這是一個 HTTP 端點上的順帶查詢：逾時很短、失敗就算了、結果有快取。盯盤迴圈完全碰不
到它，而這個檔案裡沒有任何一行會拋例外到呼叫端。
"""

from __future__ import annotations

import re
import time

import httpx

from app.config import settings
from app.services import build_info

# 六小時。GitHub 沒登入的限流是每小時 60 次，而這是他每打開一次系統狀態頁就會跑一
# 次的查詢——沒有快取的話，一個開著頁面的分頁就能把額度用完，然後真的需要知道的時
# 候問不到。
_TTL_SEC = 6 * 60 * 60

# 短。另一端是一個等著看頁面的人，而這只是那一頁上的一格。
_TIMEOUT_SEC = 5.0

_cache: tuple[float, dict] | None = None


def forget() -> None:
    """丟掉快取。測試用；正式環境不需要呼叫。"""
    global _cache
    _cache = None
    _known.clear()


def _unknown(why: str, running: str | None = None) -> dict:
    return {"running": running, "latest": None, "behind": None, "why": why}


def _fetch_latest(repo: str) -> str | None:
    """`stable` 現在指向哪一個 commit。抓不到就是 None，不拋。

    問的是 `stable` 而不是 `main`：main 上的東西可能還沒跑完測試，而告訴使用者
    「你落後了」然後讓他去拿一個沒驗過的版本，比不告訴他更糟。
    """
    response = httpx.get(
        f"https://api.github.com/repos/{repo}/commits/stable",
        headers={"Accept": "application/vnd.github+json"},
        timeout=_TIMEOUT_SEC,
        follow_redirects=True,
    )
    response.raise_for_status()
    sha = response.json().get("sha")
    return str(sha)[:7] if sha else None


def status() -> dict:
    """`{"running": …, "latest": …, "behind": True|False|None, "why": …}`。

    `behind` 是 None 的時候，**畫面上不可以顯示成「已經是最新」**。那是「不知道」。
    """
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < _TTL_SEC:
        return dict(_cache[1])

    running = build_info.commit()
    repo = (settings.UPDATE_CHECK_REPO or "").strip()

    if not repo:
        # 關掉是一個有效的設定，不是錯誤（#51）。不想讓這個 app 連外的人，把它設
        # 成空字串就好。
        answer = _unknown("版本檢查關掉了（UPDATE_CHECK_REPO 是空的）。", running)
    elif not running:
        # 有些平台不告訴容器它建的是哪一個 commit。比不出來就說比不出來——猜一個回
        # 去比不回答更糟。
        answer = _unknown(
            "這個平台沒有告訴這個 app 它是哪一個版本，所以比不出來。設定 APP_GIT_COMMIT 就能比。",
            running,
        )
    else:
        try:
            latest = _fetch_latest(repo)
        except Exception as exc:  # noqa: BLE001 -- 抓不到就是不知道，不是壞掉
            answer = _unknown(f"問不到最新版本（{type(exc).__name__}）。", running)
        else:
            if not latest:
                answer = _unknown("問到的回應裡沒有版本編號。", running)
            else:
                answer = {
                    "running": running,
                    "latest": latest,
                    "behind": latest != running,
                    "why": None,
                }

    _cache = (now, answer)
    return dict(answer)


# 一個 commit 在上游存不存在，問過就記住。
#
# 前端的 commit 每次建置才變一次，而這是每個訪客打開頁面都會觸發的查詢——沒有快取的
# 話，一個開著的分頁就能把 GitHub 沒登入的額度（每小時 60 次）用完。
_known: dict[str, bool] = {}

# 七到四十碼十六進位，跟 build_info.commit() 同一個格式。
_SHA_ONLY = re.compile(r"^[0-9a-f]{7,40}$", re.IGNORECASE)


def is_from_upstream(sha: str) -> bool | None:
    """這個 commit 在上游的 repo 裡存在嗎。`None` 代表問不到。

    ＊ 為什麼要問這個。

    這個更新模型有一個必然的分岔點：使用者（或他的 AI）動了骨架本身的原始碼。那一
    刻 `sync-from-upstream.yml` 會停下來（它只快轉、絕不覆蓋），而從此他再也拿不到
    任何更新——包括安全修補。

    而畫面上如果說「有新版可以更新」，他照著做（重新部署）拿到的還是自己那一版，
    因為同步根本沒跑。重試幾次之後他會放棄，而真正該告訴他的那件事從頭到尾沒有說
    出口。

    ＊ None 不是 False。

    誤判成分岔比誤判成落後更糟：那句話會告訴他「自動更新對你沒用」，而如果那是假
    的，他會從此不再期待更新。
    """
    if sha in _known:
        return _known[sha]

    # **這個值是輸入**（前端送上來的），而它會被拼進一個 URL。不驗格式的話，一個帶
    # 著 `../` 或問號的字串就能改變那個請求問的是什麼——而這裡是這個 app 裡少數會主
    # 動對外連線的地方。
    if not _SHA_ONLY.match(sha or ""):
        return None

    repo = (settings.UPDATE_CHECK_REPO or "").strip()
    if not repo:
        return None

    try:
        response = httpx.get(
            f"https://api.github.com/repos/{repo}/commits/{sha}",
            headers={"Accept": "application/vnd.github+json"},
            timeout=_TIMEOUT_SEC,
            follow_redirects=True,
        )
    except Exception:  # noqa: BLE001 -- 問不到就是不知道
        return None

    if response.status_code == 404:
        answer = False
    elif response.is_success:
        answer = True
    else:
        # 限流、暫時性錯誤——那些是「問不到」，不是「不存在」。
        return None

    _known[sha] = answer
    return answer


def changes_since(sha: str) -> list[dict]:
    """從他跑的那一版到 `stable` 之間改了什麼。

    ＊ 為什麼用 commit 訊息而不是另外維護一份 changelog。

    這個 repo 的 commit 訊息第一行本來就是人話（「解藥要放在病灶旁邊」、「暖身也是
    資料，也會洩漏」）。另外維護一份會有兩個事實來源，而沒同步的那一份會安靜地過
    期——這正是 #47 拒絕開第二個 repo 的同一個理由。

    ＊ 只取第一行。

    這裡的 commit 訊息是長篇的。整段丟到畫面上，使用者看到的是一面牆而不是一份清
    單，而那跟沒給一樣。

    ＊ 空清單不代表「沒有更新」。

    比不出來（分岔了、問不到）也是空的。**「為什麼是空的」由 is_from_upstream 回
    答**，這裡只負責不要炸掉。
    """
    if not _SHA_ONLY.match(sha or ""):
        return []
    repo = (settings.UPDATE_CHECK_REPO or "").strip()
    if not repo:
        return []

    try:
        response = httpx.get(
            f"https://api.github.com/repos/{repo}/compare/{sha}...stable",
            headers={"Accept": "application/vnd.github+json"},
            timeout=_TIMEOUT_SEC,
            follow_redirects=True,
        )
        if not response.is_success:
            # 404 代表那個 commit 不在上游——也就是這一份分岔了。那不是錯誤。
            return []
        commits = response.json().get("commits") or []
    except Exception:  # noqa: BLE001 -- 問不到就是沒有清單，不是壞掉
        return []

    changes = []
    for entry in commits:
        message = (entry.get("commit") or {}).get("message") or ""
        title = message.split("\n", 1)[0].strip()
        if not title:
            continue
        changes.append(
            {
                "sha": str(entry.get("sha") or "")[:7],
                "title": title,
                "at": ((entry.get("commit") or {}).get("author") or {}).get("date"),
            }
        )
    return changes
