"""「去你部署前端的平台按一次重新部署」——他沒有那個平台。

＊ 這句話是為另一種部署寫的。

有兩種形狀：

    一次部署   前端和後端在同一個映像檔裡（#53 之後的按鈕那條路，多數人）
    兩次部署   後端在 Render、前端在 Vercel（維護者自己那一份、以及分開部署的人）

第二種才會有「前端比後端舊」——那是它們各自更新的必然結果，而修法確實是去前端那個
平台按重新部署。

**第一種不可能有那件事。** 同一個映像檔裡的兩半依建構為真是同一個 commit。所以第一
種的人看到這句話的時候，唯一可能的原因是**他瀏覽器裡那份 bundle 是舊的**（#92 那個
啟發式快取），而那時候：

- 「前端跟後端是分開部署的」是假的；
- 「去你部署前端的平台按一次重新部署」指向一個不存在的東西；
- 而真正有效的動作——重新整理一次——沒有人告訴他。

他會照著去找那個平台，找不到，然後得到「這個 app 壞了而我修不好」。

＊ 判準是拿誰去比。

分開部署要比的是**上游最新的**：前端可能比後端舊好幾個月，而那正是要抓的。
一次部署要比的是**這台伺服器自己跑的那一版**：伺服器上就是新的，只有瀏覽器手上是
舊的，所以差別出現在這一格上。
"""

import pytest

from app.services import build_info, update_check


@pytest.fixture
def _serving_its_own_frontend(tmp_path, monkeypatch):
    from app import main

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    monkeypatch.setattr(main, "FRONTEND_DIST", dist)


def _update(auth_client, **params) -> dict:
    return auth_client.get("/api/system/status", params=params).json()["update"]


def test_a_deployment_that_serves_its_own_frontend_says_so(auth_client, _serving_its_own_frontend):
    """畫面沒有辦法自己知道這件事——它在哪裡被送出來的，只有伺服器知道。"""
    assert _update(auth_client)["serves_its_own_frontend"] is True


def test_a_backend_without_a_bundle_says_so_too(auth_client):
    """前端另外部署的那一份，這個目錄不存在。

    conftest 預設就是這個狀態（對齊 CI 的後端 job）。
    """
    assert _update(auth_client)["serves_its_own_frontend"] is False


def test_it_also_says_which_commit_this_server_is_running(
    auth_client, _serving_its_own_frontend, monkeypatch
):
    """一次部署的人要比的是「這台伺服器自己跑的那一版」，不是上游最新的。

    伺服器上就是新的，只有瀏覽器手上是舊的——差別只出現在這一格上。
    """
    monkeypatch.setattr(build_info, "commit", lambda: "abc1234")
    # update_check 有五分鐘的快取（不要每次開這一頁都去打 GitHub）。不清掉的話這條
    # 測試量到的是別條測試留下的答案。
    monkeypatch.setattr(update_check, "_cache", None)

    assert _update(auth_client)["running"] == "abc1234"
