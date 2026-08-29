"""後端直接供應前端，所以只要部署一次。

＊ 為什麼要這樣。

原本要部署兩次：後端（Render／Railway／Fly.io／自己的機器都行）加前端（只給了
Vercel）。那個不對稱有兩個後果：

  一、引導頁對後端給了三個選擇，對前端只給一個——而使用者早就問過同一句話：
      「render 只是其一的解法不是嗎？」
  二、更新的路徑被綁在 Vercel 上：sync-from-upstream.yml 只在「前端是一份 GitHub
      複製品而且 Actions 開著」的時候有用。

後端供應前端之後，每一條**後端**的路都自動涵蓋前端，而那些路本來就是通用的。

＊ 這不是拿掉 Vercel，是拿掉「必須再部署一次」。

想把前端另外放的人照樣可以：設 VITE_API_BASE_URL 指向他的後端就好。少掉的是**要
求**，不是選擇。

＊ 這一組守的是「不可以安靜地壞掉」的那幾條。

靜態檔掛在 `/` 上，而 `/` 會吃掉所有沒被前面的路由接走的路徑。掛錯順序的話：

    /api/... 被靜態檔吃掉  → 前端拿到 HTML，而它在等 JSON。錯誤訊息會是
                             「Unexpected token '<'」，跟真正的原因差了十萬八千里。
    dist 不存在就開不了機  → 而這個 app 的鐵律是警告不能停擺。開發環境、還沒建過
                             前端的映像檔，都不可以讓 API 起不來。
"""

import pytest


def test_the_api_is_not_shadowed_by_the_static_files(client):
    """**這一條最重要。**

    靜態檔掛在 `/`，而如果它排在 API 前面，`/api/...` 會拿到 index.html。前端收到
    HTML 而它在等 JSON，錯誤訊息是「Unexpected token '<'」——一個跟真正原因差了十
    萬八千里的訊息。
    """
    resp = client.get("/api/setup/status")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")


def test_an_unknown_api_path_is_a_json_404_not_the_app_shell(client):
    """不存在的 API 路徑要回 JSON 的 404，不是 index.html。

    回 HTML 的話，一個打錯的網址會讓前端以為請求成功了，然後在解析的時候炸掉。而
    這種錯誤最常發生在「後端更新了、前端還是舊的」的時候——正是這整套機制在處理的
    情況。
    """
    resp = client.get("/api/this-does-not-exist")

    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")


def test_healthz_is_not_shadowed_either(client):
    """外部看門狗每五分鐘打它一次，而它是「這個部署還活著嗎」唯一的答案。"""
    resp = client.get("/healthz")

    assert resp.status_code in (200, 503)
    assert resp.headers["content-type"].startswith("application/json")


def test_the_app_starts_without_a_built_frontend(client):
    """**沒有 dist 也要起得來。**

    開發環境沒有建過前端；一個只建後端的映像檔也沒有。而這個 app 的鐵律是警告不能
    停擺——一個因為找不到靜態檔而起不來的 API，是把「畫面沒了」升級成「提醒沒了」。

    這一條在測試環境裡本來就成立（測試不會去建前端），所以它其實是在守「不要哪天
    有人把它改成必需的」。
    """
    resp = client.get("/healthz")

    assert resp.status_code in (200, 503)


def test_a_frontend_route_falls_back_to_the_app_shell(client, tmp_path, monkeypatch):
    """`/strategies` 這種前端路由要回 index.html。

    單頁應用的路由在瀏覽器裡，不在伺服器上。使用者按 F5 重新整理 `/strategies`，
    伺服器上沒有那個檔案——回 404 的話他看到的是一個壞掉的網站，而他什麼都沒做錯。
    """
    from app import main

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>app</title>", encoding="utf-8")
    monkeypatch.setattr(main, "FRONTEND_DIST", dist)

    resp = client.get("/strategies")

    assert resp.status_code == 200
    assert "<!doctype html>" in resp.text.lower()


def test_a_real_static_file_is_served_as_itself(client, tmp_path, monkeypatch):
    """真的存在的檔案要照原樣送出去，不要被 SPA 的 fallback 吃掉。"""
    from app import main

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setattr(main, "FRONTEND_DIST", dist)

    resp = client.get("/assets/app.js")

    assert resp.status_code == 200
    assert "console.log(1)" in resp.text


@pytest.mark.parametrize("path", ["/api/nope", "/healthz", "/ws"])
def test_the_backend_owns_these_prefixes_forever(client, tmp_path, monkeypatch, path):
    """就算 dist 存在，這幾個前綴也永遠是後端的。

    列出來而不是靠「排在前面就好」：排序是一個沒有東西守著的約定，而它壞掉的方式
    是靜默的——前端會拿到 HTML 然後在解析時炸掉。
    """
    from app import main

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    monkeypatch.setattr(main, "FRONTEND_DIST", dist)

    resp = client.get(path)

    assert "text/html" not in resp.headers.get("content-type", "")
