"""改過骨架的副本，要說「你改過」，不是說「你落後了」。

＊ 為什麼這兩件事必須分開。

這個更新模型是：骨架由上游修，使用者自己管他加的東西。而那個模型有一個必然的分岔
點——**他（或他的 AI）動了骨架本身的原始碼**。

那一刻，`sync-from-upstream.yml` 會停下來（它只快轉、絕不覆蓋），而從此他再也拿不到
任何更新，包括安全修補。

現在畫面上會說「有新版可以更新」。那句話對這種情況是**錯的**：

    他照著做（重新部署）→ 拿到的還是他自己那一版，因為同步根本沒跑
    他重試幾次之後放棄 → 而真正該告訴他的那件事，從頭到尾沒有說出口

正確的話是：「你改過骨架，所以自動更新停了。那是你的選擇，但安全修補也不會到。」

＊ 怎麼分辨。

問 GitHub：這個 commit 在上游的 repo 裡存在嗎。

    存在但不是 stable  → 落後。等同步，或按一次重新部署
    不存在             → 這一份分岔了

不存在的原因不一定是他改的（也可能是我們還沒推上去的本機建置），但對使用者來說結論
一樣：**這個 commit 不是從上游來的，所以自動更新對它沒有用。**
"""

import httpx
import pytest

from app.services import update_check


@pytest.fixture(autouse=True)
def _fresh_cache():
    update_check.forget()
    yield
    update_check.forget()


def _github(known: set[str], stable: str):
    """假的 GitHub：known 裡的 commit 存在，其他回 404。"""

    def _get(url, **kwargs):
        request = httpx.Request("GET", url)
        if url.endswith("/commits/stable"):
            return httpx.Response(200, json={"sha": stable}, request=request)
        sha = url.rsplit("/", 1)[-1]
        if sha in known:
            return httpx.Response(200, json={"sha": sha}, request=request)
        return httpx.Response(404, json={"message": "Not Found"}, request=request)

    return _get


def test_a_commit_that_exists_upstream_is_just_behind(monkeypatch):
    monkeypatch.setattr(httpx, "get", _github(known={"aaaaaaa"}, stable="bbbbbbb"))

    assert update_check.is_from_upstream("aaaaaaa") is True


def test_a_commit_that_does_not_exist_upstream_means_this_copy_diverged(monkeypatch):
    """**這是這一塊的全部意義。**

    這個 commit 不是從上游來的，所以自動更新對它沒有用——而叫他「按一次重新部署」
    只會讓他拿到自己那一版，然後以為更新壞掉了。
    """
    monkeypatch.setattr(httpx, "get", _github(known={"bbbbbbb"}, stable="bbbbbbb"))

    assert update_check.is_from_upstream("ffffff0") is False


def test_not_being_able_to_ask_is_not_an_answer(monkeypatch):
    """問不到就是不知道，不是「分岔了」。

    誤判成分岔的後果比誤判成落後更糟：那句話會告訴他「自動更新對你沒用」，而如果那
    是假的，他會從此不再期待更新——包括安全修補。
    """

    def _boom(url, **kwargs):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(httpx, "get", _boom)

    assert update_check.is_from_upstream("aaaaaaa") is None


def test_a_nonsense_commit_is_never_sent_to_github(monkeypatch):
    """這個值來自前端送上來的東西，所以它是**輸入**。

    它會被拼進一個 URL。不驗格式的話，一個帶著 `../` 或問號的字串就能改變那個請求
    問的是什麼——而這一段程式碼是這個 app 裡少數會主動對外連線的地方。
    """
    called: list[str] = []

    def _spy(url, **kwargs):
        called.append(url)
        return httpx.Response(200, json={"sha": "x"}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", _spy)

    assert update_check.is_from_upstream("../../etc/passwd") is None
    assert update_check.is_from_upstream("") is None
    assert update_check.is_from_upstream("aaaaaaa?foo=bar") is None
    assert called == [], f"把一個不是 commit 的東西送去 GitHub 了：{called}"


def test_it_does_not_ask_twice_for_the_same_commit(monkeypatch):
    """前端的 commit 每次建置才變一次，而這是每個訪客都會觸發的查詢。"""
    calls: list[str] = []

    def _counted(url, **kwargs):
        calls.append(url)
        return httpx.Response(200, json={"sha": "aaaaaaa"}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", _counted)

    for _ in range(4):
        update_check.is_from_upstream("aaaaaaa")

    assert len(calls) == 1, f"問了 {len(calls)} 次"


# --- 端點 --------------------------------------------------------------------


def test_the_status_endpoint_answers_for_the_frontends_own_commit(auth_client, monkeypatch):
    """前端問「我這一版是不是從上游來的」。

    這個問題只有前端問得出來：它的 commit 是建置期常數，後端不知道。
    """
    monkeypatch.setattr(httpx, "get", _github(known={"aaaaaaa"}, stable="bbbbbbb"))

    body = auth_client.get("/api/system/status?frontend_commit=aaaaaaa").json()

    assert body["update"]["frontend_from_upstream"] is True


def test_a_diverged_frontend_is_reported_as_diverged(auth_client, monkeypatch):
    monkeypatch.setattr(httpx, "get", _github(known={"bbbbbbb"}, stable="bbbbbbb"))

    body = auth_client.get("/api/system/status?frontend_commit=ffffff0").json()

    assert body["update"]["frontend_from_upstream"] is False


def test_not_asking_is_not_an_error(auth_client, monkeypatch):
    """沒帶那個參數的呼叫端（例如舊的前端）不可以壞掉。

    前端會比後端晚更新——那正是這整件事在處理的問題，所以這個端點必須對一個還沒學
    會帶參數的前端照常回答。
    """
    monkeypatch.setattr(httpx, "get", _github(known={"bbbbbbb"}, stable="bbbbbbb"))

    body = auth_client.get("/api/system/status").json()

    assert body["update"]["frontend_from_upstream"] is None


# --- 「這一版之後改了什麼」 ---------------------------------------------------


def _compare(commits: list[tuple[str, str]], status: int = 200):
    def _get(url, **kwargs):
        request = httpx.Request("GET", url)
        if "/compare/" in url:
            if status != 200:
                return httpx.Response(status, json={"message": "no"}, request=request)
            return httpx.Response(
                200,
                json={
                    "commits": [
                        {
                            "sha": sha,
                            "commit": {
                                "message": message,
                                "author": {"date": "2026-08-01T00:00:00Z"},
                            },
                        }
                        for sha, message in commits
                    ]
                },
                request=request,
            )
        return httpx.Response(200, json={"sha": "bbbbbbb"}, request=request)

    return _get


def test_it_lists_what_changed_since_his_version(monkeypatch):
    """他要看的是「我這一版之後改了什麼」，不是整個專案的歷史。

    compare API 回的正好是那一段：base 是他跑的那一版，head 是 stable。
    """
    monkeypatch.setattr(
        httpx,
        "get",
        _compare([("ccc1111", "修好圖表往前拉沒有資料"), ("ddd2222", "策略搬進子行程")]),
    )

    changes = update_check.changes_since("aaaaaaa")

    assert [c["title"] for c in changes] == ["修好圖表往前拉沒有資料", "策略搬進子行程"]


def test_only_the_first_line_of_each_message(monkeypatch):
    """commit 訊息在這個 repo 裡是長篇的——第一行才是人話。

    整段丟到畫面上，使用者看到的是一面牆而不是一份清單，而那跟沒給一樣。
    """
    monkeypatch.setattr(
        httpx,
        "get",
        _compare([("ccc1111", "修好圖表\n\n很長的說明\n又一段\n\nRefs #12")]),
    )

    changes = update_check.changes_since("aaaaaaa")

    assert changes[0]["title"] == "修好圖表"


def test_a_diverged_copy_gets_an_empty_list_not_a_crash(monkeypatch):
    """比不出來就是比不出來。

    改過骨架的副本，它的 commit 在上游不存在，compare 會回 404。那不是錯誤，那是
    「這一份分岔了」——而那件事由 is_from_upstream 負責講，這裡只要不要炸掉。
    """
    monkeypatch.setattr(httpx, "get", _compare([], status=404))

    assert update_check.changes_since("ffffff0") == []


def test_it_never_raises(monkeypatch):
    def _boom(url, **kwargs):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(httpx, "get", _boom)

    assert update_check.changes_since("aaaaaaa") == []


def test_a_nonsense_commit_is_not_sent_to_github(monkeypatch):
    called: list[str] = []

    def _spy(url, **kwargs):
        called.append(url)
        return httpx.Response(200, json={"commits": []}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", _spy)

    assert update_check.changes_since("../../etc/passwd") == []
    assert called == []


def test_the_changes_endpoint_needs_a_login(client):
    """稽查的硬性關卡。而且這條路會對外連線——不可以開給沒登入的人。"""
    resp = client.get("/api/system/updates")

    assert resp.status_code in (401, 403), resp.status_code


def test_the_changes_endpoint_lists_them(auth_client, monkeypatch):
    monkeypatch.setattr(update_check.build_info, "commit", lambda: "aaaaaaa")
    monkeypatch.setattr(httpx, "get", _compare([("ccc1111", "修好圖表往前拉沒有資料")]))

    body = auth_client.get("/api/system/updates").json()

    assert body["changes"][0]["title"] == "修好圖表往前拉沒有資料"


def test_not_knowing_our_own_version_gives_no_list_rather_than_a_wrong_one(
    auth_client, monkeypatch
):
    """有些平台不告訴容器它建的是哪一個 commit。

    那時候「從哪一版到哪一版」根本問不出來。回一個看起來像清單的東西——例如整個專案
    的歷史——比不回答更糟：他會以為那是「我還沒拿到的更新」。
    """
    monkeypatch.setattr(update_check.build_info, "commit", lambda: None)
    called: list[str] = []

    def _spy(url, **kwargs):
        called.append(url)
        return httpx.Response(200, json={"commits": []}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", _spy)

    body = auth_client.get("/api/system/updates").json()

    assert body["changes"] == []
    assert called == [], "不知道自己是哪一版，卻還是去問了 GitHub"


def test_it_is_a_separate_endpoint_from_status(auth_client, monkeypatch):
    """**不要塞進 /status。**

    那一頁會被輪詢，而 compare 是一次對 GitHub 的呼叫。塞在一起的話，一個開著的分
    頁就能把沒登入的額度（每小時 60 次）用完——然後真的需要知道的時候問不到。
    """
    calls: list[str] = []

    def _spy(url, **kwargs):
        calls.append(url)
        return httpx.Response(200, json={"sha": "bbbbbbb"}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", _spy)

    auth_client.get("/api/system/status")

    assert not any("/compare/" in url for url in calls), "狀態頁去打了 compare"


def test_the_status_function_is_callable_directly(db_session, monkeypatch):
    """`system_status` **也被 system_assist 直接呼叫**，不是只走路由。

    而 FastAPI 的參數預設值只有走路由時才會被解析。所以一個寫成
    `= Query(default=None)` 的參數，在直接呼叫時拿到的是那個哨兵物件本身——它會一
    路流進正規表達式然後炸掉。

    這次加 frontend_commit 的時候就是這樣紅了七條，而紅的地方是 test_system_assist，
    一個看起來跟版本檢查毫無關係的檔案。修法是 Annotated（純粹的預設值就是 None），
    而這一條守著那個修法不被改回去。
    """
    from app.api.routers.system import system_status
    from app.models.user import User

    user = User(email="direct-call@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    monkeypatch.setattr(httpx, "get", _github(known={"bbbbbbb"}, stable="bbbbbbb"))

    # 就是 system_assist 那一行的形狀：沒有 frontend_commit。
    body = system_status(db=db_session, user=user)

    assert body["update"]["frontend_from_upstream"] is None
