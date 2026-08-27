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
