"""Can the person who deployed this tell WHICH BUILD is running?

WHY THIS EXISTS. The release path is automatic now: CI's deploy job calls the
host's deploy hook once every other job is green, and nobody presses anything.
Automatic is exactly when nobody looks -- and the failure it replaces was
invisible from the outside. The deployed backend ran code older than main
twice in one session while every probe answered 「ok」, because an old build is
perfectly healthy. It is just old.

So the probe carries the build's identity, and 「did the deploy actually
arrive?」 becomes a question with an answer instead of a guess.

ON THE UNAUTHENTICATED ENDPOINT, deliberately. The moment you most need to
know what is running is the moment a deploy shipped something broken -- and a
broken build is one you may not be able to log in to. A commit hash is not a
secret: it identifies a build, it does not describe one, and the repository
this is built from is public by design.

VENDOR-NEUTRAL BY CONSTRUCTION. The commit arrives in an environment variable
and this app does not care whose. `APP_GIT_COMMIT` is the name it documents
and the one a self-hoster sets; the host-specific names are recognised only so
that somebody who deploys on a platform that already provides one has nothing
to configure at all.
"""

import re

import pytest

from app.services import build_info

# Every name this app will look at. The tests clear all of them before each
# case: CI itself sets GITHUB_SHA, and a test asserting 「no variable means no
# claim」 would otherwise pass on a laptop and fail in Actions.
ALL_NAMES = (
    "APP_GIT_COMMIT",
    "GIT_COMMIT",
    "GITHUB_SHA",
    "RENDER_GIT_COMMIT",
    "SOURCE_VERSION",
    "RAILWAY_GIT_COMMIT_SHA",
    "KOYEB_GIT_SHA",
    "VERCEL_GIT_COMMIT_SHA",
)

SHA = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture(autouse=True)
def _no_inherited_commit(monkeypatch):
    for name in ALL_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_healthz_says_which_commit_is_running(client, monkeypatch):
    """The whole point: push a commit, wait, ask the deployment what it is."""
    monkeypatch.setenv("APP_GIT_COMMIT", SHA)

    version = client.get("/healthz").json()["version"]

    assert version["commit"] == SHA[:7]


def test_the_documented_name_wins_over_any_host_variable(client, monkeypatch):
    """Nobody is bound to one host. Setting the app's own variable has to
    override whatever the platform happens to inject, or a self-hoster cannot
    correct a wrong value."""
    monkeypatch.setenv("RENDER_GIT_COMMIT", "f" * 40)
    monkeypatch.setenv("APP_GIT_COMMIT", SHA)

    assert build_info.commit() == SHA[:7]


@pytest.mark.parametrize(
    "name",
    ["GIT_COMMIT", "GITHUB_SHA", "RENDER_GIT_COMMIT", "SOURCE_VERSION", "RAILWAY_GIT_COMMIT_SHA"],
)
def test_a_host_that_already_provides_one_needs_no_configuration(monkeypatch, name):
    """Render calls it RENDER_GIT_COMMIT, Heroku and Dokku call it
    SOURCE_VERSION, Railway calls it RAILWAY_GIT_COMMIT_SHA. Reading all of
    them costs one tuple and saves the deployer a step they would have to be
    told about."""
    monkeypatch.setenv(name, SHA)

    assert build_info.commit() == SHA[:7]


def test_no_variable_means_no_claim(monkeypatch):
    """Silence, not a plausible-looking placeholder. 「unknown」 in this field
    would be indistinguishable from a real answer at a glance, and the one
    thing this must never do is let somebody believe a deploy landed when
    nothing here knows whether it did."""
    assert build_info.commit() is None


def test_an_empty_variable_is_not_a_commit(monkeypatch):
    """A Dockerfile ARG that nobody passed leaves the variable set and empty,
    which is the same as absent."""
    monkeypatch.setenv("APP_GIT_COMMIT", "   ")

    assert build_info.commit() is None


@pytest.mark.parametrize(
    "planted",
    [
        "postgresql://trader:hunter2@db.example.com/trading",  # a DSN
        "not-a-sha",
        "abc",  # too short to be an abbreviated hash
        "0123456789abcdef0123456789abcdef012345678901",  # too long
        "<script>alert(1)</script>",
        "a" * 39 + "z",  # right length, not hex
    ],
)
def test_a_public_endpoint_never_echoes_something_that_is_not_a_commit(
    client, monkeypatch, planted
):
    """This field reads an environment variable and serves it to the internet
    with no credentials. Whatever ends up in that variable -- a mistake, a
    paste of the wrong value, a deliberate plant -- must not come back out.
    Only the shape of a git hash is ever repeated."""
    monkeypatch.setenv("APP_GIT_COMMIT", planted)

    version = client.get("/healthz").json()["version"]

    assert version["commit"] is None
    assert planted not in client.get("/healthz").text


def test_the_shape_is_a_short_hash(client, monkeypatch):
    """Seven hex characters, so it can be compared against `git rev-parse
    --short HEAD` by eye without copying anything."""
    monkeypatch.setenv("APP_GIT_COMMIT", SHA.upper())

    commit = client.get("/healthz").json()["version"]["commit"]

    assert re.fullmatch(r"[0-9a-f]{7}", commit)


def test_started_at_is_when_the_process_started_not_now(client):
    """A restart is the other half of the answer, and the half that works on
    every host including the ones that inject nothing: a deploy that landed
    moves this forward. So it has to be the process's start, not the clock."""
    first = client.get("/healthz").json()["version"]["started_at"]
    second = client.get("/healthz").json()["version"]["started_at"]

    assert first == second
    assert first.endswith("Z")  # UTC, said out loud, not a naive local time


def test_the_setup_mode_probe_carries_the_version_too(client, monkeypatch):
    """An unconfigured deployment is answered by the middleware, not the health
    router -- and it is the likeliest place for somebody to be asking 「is this
    even the build I just pushed?」, because a first deploy has nothing else to
    look at."""
    monkeypatch.setattr("app.main.SETUP_MODE_REASON", "尚未設定完成")
    monkeypatch.setenv("APP_GIT_COMMIT", SHA)

    body = client.get("/healthz").json()

    assert body["status"] == "setup"
    assert body["version"]["commit"] == SHA[:7]


def test_the_image_hands_the_frontend_build_a_name_it_actually_reads():
    """**兩邊的環境變數名字要一樣，而在這之前它們不一樣。**

    Dockerfile 設的是 `ENV VITE_APP_COMMIT=...`，`vite.config.ts` 讀的是
    `process.env.APP_GIT_COMMIT`。名字對不起來，所以映像檔裡建出來的前端，版本永遠是
    空的——徽章永遠顯示「不知道」。

    這不會讓任何東西變紅：建置成功、bundle 正常、畫面能用。`frontendCommit()` 也做了
    正確的事（空的就是 null，不是「最新」）。只是那個值從來沒有到過。而這整個徽章存在
    的理由，就是讓「沒跟上更新」這件事在 app 裡看得見。

    量出來的方式很土：本機跑 `APP_GIT_COMMIT=x npx vite build`，然後 grep dist。
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent.parent
    dockerfile = (root / "backend" / "Dockerfile").read_text(encoding="utf-8")
    vite_config = (root / "frontend" / "vite.config.ts").read_text(encoding="utf-8")

    # **只看前端那個 stage。** 整份檔案一起看的話這一條會是綠的而 bug 還在：後端那
    # 一段設的是對的名字（ENV APP_GIT_COMMIT），前端那一段設的是 VITE_APP_COMMIT，
    # 而兩段互相看不到對方的 ENV。第一版的我就是這樣寫的，它一次就綠了。
    stages = re.split(r"^FROM\s+", dockerfile, flags=re.MULTILINE)
    frontend_stage = next((st for st in stages if st.lower().startswith("node:")), None)
    assert frontend_stage, "Dockerfile 不再有建前端的那個 stage 了"

    injected = re.findall(r"^ENV\s+([A-Z_]+)=", frontend_stage, re.MULTILINE)
    assert injected, "前端那個 stage 不再注入任何建置期變數了？"

    # vite.config.ts 真的會去讀的名字。
    read_by_vite = set(re.findall(r"process\.env\.([A-Z_]+)", vite_config))
    assert read_by_vite, "vite.config.ts 不再從環境變數讀版本了"

    assert read_by_vite & set(injected), (
        f"Dockerfile 注入 {injected}，但 vite.config.ts 只讀 {sorted(read_by_vite)}——"
        "名字對不起來，映像檔裡的前端版本會永遠是空的"
    )


# --- 畫面跟 API 出自同一個映像檔的時候，前端的版本不必另外問 -------------------
#
# 「這一份被改過嗎」需要**前端自己的** commit，而那是建置期常數：前端帶上來，後端不
# 知道。分開部署的時候（Vercel 會給 VERCEL_GIT_COMMIT_SHA）這是唯一的辦法。
#
# 但畫面現在可以跟 API 出自同一個映像檔（#53）。那時候兩者是同一個 commit **依建構
# 為真**——前端的 dist 就是這個容器裡的那一份。而建置期的那個值需要平台把
# APP_GIT_COMMIT 當 build arg 傳進來，那是一個平台不見得會做、而且我們無法保證的
# 動作。少了它，「這一份被改過」這個偵測就完全不會動——安靜地，沒有東西會變紅。
#
# 所以那個問題在這種部署上由後端自己回答。這不是猜：dist 在這個映像檔裡，而映像檔是
# 從某一個 commit 建出來的。


def test_a_bundled_frontend_gets_its_version_from_the_image_it_shipped_in(
    auth_client, monkeypatch, tmp_path
):
    """沒有帶 frontend_commit，但這一份自己供應畫面——那就是同一個 commit。

    這一條守的是「偵測不會因為平台沒傳 build arg 就整個消失」。
    """
    from app import main
    from app.services import update_check

    update_check.forget()
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    monkeypatch.setattr(main, "FRONTEND_DIST", dist)
    monkeypatch.setenv("APP_GIT_COMMIT", "abc1234")

    asked: list[str] = []
    monkeypatch.setattr(update_check, "is_from_upstream", lambda sha: asked.append(sha) or True)

    resp = auth_client.get("/api/system/status")

    assert resp.status_code == 200, resp.text
    assert asked == ["abc1234"], "沒有拿這個映像檔自己的 commit 去問"
    assert resp.json()["update"]["frontend_from_upstream"] is True


def test_a_separately_hosted_frontend_still_speaks_for_itself(auth_client, monkeypatch, tmp_path):
    """前端有帶自己的 commit 的時候，**以它為準**。

    分開部署的那一份可能比後端舊好幾個月，那正是這個偵測要抓的情況。拿後端的
    commit 去回答會把它蓋掉，而蓋掉的方向是「看起來沒問題」——最糟的那一種。
    """
    from app import main
    from app.services import update_check

    update_check.forget()
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    monkeypatch.setattr(main, "FRONTEND_DIST", dist)
    monkeypatch.setenv("APP_GIT_COMMIT", "abc1234")

    asked: list[str] = []
    monkeypatch.setattr(update_check, "is_from_upstream", lambda sha: asked.append(sha) or True)

    auth_client.get("/api/system/status?frontend_commit=9999999")

    assert asked == ["9999999"], "前端帶了自己的版本，卻拿後端的去問"


def test_without_a_bundled_frontend_it_still_says_it_does_not_know(
    auth_client, monkeypatch, tmp_path
):
    """沒有內建前端、前端也沒帶版本——那就是不知道。

    **不知道不可以變成「沒有被改過」。** 那句話會讓他以為自動更新對他有效，而如果
    他其實分岔了，他會一直等一個永遠不會到的更新。
    """
    from app import main
    from app.services import update_check

    update_check.forget()
    monkeypatch.setattr(main, "FRONTEND_DIST", tmp_path / "there-is-no-dist-here")
    monkeypatch.setenv("APP_GIT_COMMIT", "abc1234")

    resp = auth_client.get("/api/system/status")

    assert resp.json()["update"]["frontend_from_upstream"] is None
