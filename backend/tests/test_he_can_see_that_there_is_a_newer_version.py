"""使用者要看得見「我這一份是不是舊的」。

＊ 為什麼這件事重要。

他的副本是從我們的 repo 部署的，而我們每一次改動都是他機器上的一次更新——他不在
場、沒有 CI、也不知道我們改了什麼。這一輪修掉的兩個洞（#50、#51）就是例子：一個在
更新時會停掉他每一支策略，一個會停掉整個 app。

自動更新（`stable` ＋ autoDeploy）解決的是「新部署的人」。但關掉自動更新的人、以及
前端那半（Vercel 的 clone 會複製一份 repo，來源就斷了）都還在外面。**看得見**是那
些人唯一的路。

＊ 這一頁最容易做錯的地方：把「不知道」說成一個答案。

抓不到 GitHub（他的機器連不出去、GitHub 掛了、被限流）的時候，唯一誠實的答案是
「不知道」。說成「已經是最新」會讓他錯過安全修補；說成「有新版」會讓他為了一個不
存在的更新去重新部署，而重新部署有它自己的風險。

同一條規則也適用在「我不知道自己是哪一版」：build_info.commit() 在沒有
APP_GIT_COMMIT 的平台上回 None，那時候比不出任何東西，就要說比不出來。

＊ 而且它絕對不可以影響到提醒。

這是一個 HTTP 端點上的順帶查詢，逾時很短、失敗就算了、結果有快取。盯盤迴圈完全碰
不到它。
"""

import httpx
import pytest

from app.services import update_check


@pytest.fixture(autouse=True)
def _fresh_cache():
    update_check.forget()
    yield
    update_check.forget()


def _answer(sha: str):
    def _fake(url, **kwargs):
        return httpx.Response(200, json={"sha": sha}, request=httpx.Request("GET", url))

    return _fake


def test_same_commit_means_up_to_date(monkeypatch):
    monkeypatch.setattr(update_check.build_info, "commit", lambda: "aaaaaaa")
    monkeypatch.setattr(httpx, "get", _answer("aaaaaaa1234567890"))

    status = update_check.status()

    assert status["behind"] is False
    assert status["running"] == "aaaaaaa"


def test_a_different_commit_means_there_is_a_newer_version(monkeypatch):
    monkeypatch.setattr(update_check.build_info, "commit", lambda: "aaaaaaa")
    monkeypatch.setattr(httpx, "get", _answer("bbbbbbb1234567890"))

    status = update_check.status()

    assert status["behind"] is True
    assert status["latest"] == "bbbbbbb"


def test_unreachable_is_not_an_answer(monkeypatch):
    """**這一條是這個功能最重要的一件事。**

    抓不到就說不知道。說成「已經是最新」會讓他錯過安全修補；說成「有新版」會讓他
    為了一個不存在的更新去重新部署，而重新部署有它自己的風險。
    """
    monkeypatch.setattr(update_check.build_info, "commit", lambda: "aaaaaaa")

    def _boom(url, **kwargs):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(httpx, "get", _boom)

    status = update_check.status()

    assert status["behind"] is None, "抓不到卻給了一個是非題的答案"
    assert status["why"], "沒說為什麼不知道"


def test_not_knowing_our_own_version_is_also_not_an_answer(monkeypatch):
    """有些平台不告訴容器它建的是哪一個 commit。

    那時候 build_info.commit() 回 None——比不出任何東西，就要說比不出來。猜一個回
    去比不回答更糟。
    """
    monkeypatch.setattr(update_check.build_info, "commit", lambda: None)
    monkeypatch.setattr(httpx, "get", _answer("bbbbbbb1234567890"))

    status = update_check.status()

    assert status["behind"] is None
    assert status["why"]


def test_it_does_not_hammer_github(monkeypatch):
    """GitHub 沒登入的限流是每小時 60 次。

    而這是一個他每打開一次系統狀態頁就會跑一次的查詢。沒有快取的話，一個開著頁面
    的分頁就能把額度用完——然後真的需要知道的時候問不到。
    """
    monkeypatch.setattr(update_check.build_info, "commit", lambda: "aaaaaaa")
    calls: list[str] = []

    def _counted(url, **kwargs):
        calls.append(url)
        return httpx.Response(200, json={"sha": "aaaaaaa1"}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", _counted)

    for _ in range(5):
        update_check.status()

    assert len(calls) == 1, f"打了 {len(calls)} 次 GitHub，應該只有一次"


def test_it_never_raises(monkeypatch):
    """呼叫端是一個 HTTP 端點，而這只是它順帶回報的一格。

    這一格壞掉不可以讓整個系統狀態頁壞掉——那一頁正是他在「東西好像怪怪的」時候會
    打開的地方。
    """
    monkeypatch.setattr(update_check.build_info, "commit", lambda: "aaaaaaa")

    def _nonsense(url, **kwargs):
        return httpx.Response(200, content=b"not json at all", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", _nonsense)

    status = update_check.status()

    assert status["behind"] is None


def test_turning_it_off_is_a_valid_configuration(monkeypatch):
    """不想讓它連外的人，把 repo 設成空字串就好。

    而那不可以變成一格必填（#51）：預設值指向這個 repo，空字串代表關掉。
    """
    from app.config import settings

    monkeypatch.setattr(settings, "UPDATE_CHECK_REPO", "")

    def _should_not_run(url, **kwargs):
        raise AssertionError("關掉了還是連出去了")

    monkeypatch.setattr(httpx, "get", _should_not_run)

    status = update_check.status()

    assert status["behind"] is None
    assert status["why"]


def test_the_status_page_reports_it(auth_client, monkeypatch):
    """它要出現在他會打開的那一頁上。

    一個只有 API 回得出來的答案，對這個使用者等於不存在。
    """
    monkeypatch.setattr(update_check.build_info, "commit", lambda: "aaaaaaa")
    monkeypatch.setattr(httpx, "get", _answer("bbbbbbb1234567890"))

    body = auth_client.get("/api/system/status").json()

    assert "update" in body, "系統狀態頁沒有回報版本落後與否"
    assert body["update"]["behind"] is True
