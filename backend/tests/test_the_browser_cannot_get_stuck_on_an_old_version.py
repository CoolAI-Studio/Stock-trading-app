"""更新送到了他的伺服器，但他的手機還在看上一版——而且可能看好幾天。

＊ 這條路的每一段都做對了，然後在最後一公尺卡住。

#52 之後，後端追 `stable`、驗證過才前進、部署有沒有送達由 CI 確認；#53 之後前端和
後端是同一個映像檔，所以兩半永遠同步。到這裡為止，「他的伺服器上跑的是最新版」是
有保證的。

**然後那份新的 `index.html` 送不到他的瀏覽器。**

`FileResponse` 不設 `Cache-Control`。沒有這個標頭的回應，瀏覽器會用 RFC 9111 §4.2.2
的**啟發式快取**：拿 `Last-Modified` 到現在的時間差的十分之一當作新鮮期。而
`Last-Modified` 就是映像檔裡那個檔案的時間，也就是**上一次建置**的時間。

    上一次建置到現在 10 天 → 這份 index.html 在他手機上可以放 1 天不問伺服器
    上一次建置到現在 60 天 → 6 天

也就是說：**我們愈久沒改東西，他卡在舊版的時間就愈長。** 而 Vite 的資產檔名是帶雜
湊的，所以舊的 `index.html` 會繼續指向舊的那一支 bundle——那支還在伺服器上，照樣送
得出來。整件事完全沒有錯誤訊息。

＊ 它還會讓一個做對的東西說錯話。

系統狀態頁會比對「這個畫面的 commit」和「上游最新的 commit」，不一樣就說**「你看到
的這個畫面是舊的，去你部署前端的平台按一次重新部署」**。而 `FRONTEND_COMMIT` 是建
置期常數，就烙在那份被快取住的 bundle 裡。

於是他讀到的是一句真話配一個沒有用的辦法：畫面確實是舊的，但伺服器上早就是新的
了，重新部署幾次都不會改變任何事。要他做的其實是清快取，而那句話沒有人說。

＊ 反過來也不可以。

`/assets/` 底下的檔名帶內容雜湊，改一個字檔名就變了。那些**應該**用力快取——不然他
每次打開 app 都要重新下載 660 KB，而他是在手機上、用行動網路。

所以規則是分開的：外殼每次問一次（有 ETag，通常是一個 304，幾十個位元組），內容永
遠不用問。
"""

import pytest

CACHE_CONTROL = "cache-control"


@pytest.fixture
def served(client, tmp_path, monkeypatch):
    """一份建好的前端，形狀跟 `npm run build` 出來的一樣。"""
    from app import main

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>app</title>", encoding="utf-8")
    (dist / "assets" / "index-CNbqHiLX.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "assets" / "index-CNbqHiLX.css").write_text("body{}", encoding="utf-8")
    (dist / "sw.js").write_text("// service worker", encoding="utf-8")
    (dist / "manifest.webmanifest").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(main, "FRONTEND_DIST", dist)
    return client


def _must_revalidate(response) -> bool:
    """瀏覽器每次都會回來問一次嗎。

    `no-cache` 的意思正是這個——不是「不要存」，是「存可以，但用之前要問」。
    """
    value = response.headers.get(CACHE_CONTROL, "").lower()
    return "no-cache" in value or "max-age=0" in value or "no-store" in value


@pytest.mark.parametrize("path", ["/", "/strategies", "/index.html"])
def test_the_shell_is_checked_every_time(served, path):
    """外殼每次問一次，不然他會卡在舊版好幾天，而且沒有任何錯誤訊息。"""
    response = served.get(path)

    assert response.status_code == 200
    assert _must_revalidate(response), response.headers.get(CACHE_CONTROL)


@pytest.mark.parametrize("path", ["/sw.js", "/manifest.webmanifest"])
def test_the_files_without_a_hash_in_their_name_are_checked_too(served, path):
    """這兩個的檔名固定，所以它們的新版跟舊版是同一個網址。

    service worker 尤其重要：它是推播真正畫出通知的那一段，而一個卡在舊版的
    service worker 會安靜地一直用舊的邏輯。
    """
    response = served.get(path)

    assert response.status_code == 200
    assert _must_revalidate(response), response.headers.get(CACHE_CONTROL)


@pytest.mark.parametrize("path", ["/assets/index-CNbqHiLX.js", "/assets/index-CNbqHiLX.css"])
def test_the_hashed_assets_are_kept(served, path):
    """檔名帶內容雜湊＝改一個字網址就變了＝舊的網址永遠是舊的內容。

    這些不用力快取的話，他每次打開 app 都要用行動網路重新下載 660 KB。
    """
    response = served.get(path)

    assert response.status_code == 200
    value = response.headers.get(CACHE_CONTROL, "").lower()
    assert "max-age=" in value and "max-age=0" not in value, value
    assert not _must_revalidate(response), value


def test_asking_again_costs_almost_nothing(served):
    """「每次問一次」要便宜，不然它就變成一個效能問題然後被拿掉。

    ETag 讓沒改的時候只回一個 304。
    """
    first = served.get("/")
    etag = first.headers.get("etag")
    assert etag, first.headers

    again = served.get("/", headers={"If-None-Match": etag})

    assert again.status_code == 304
