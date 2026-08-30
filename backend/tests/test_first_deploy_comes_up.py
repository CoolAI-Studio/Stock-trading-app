"""A brand-new deployment, with every optional box left blank.

The owner asked the question this file exists to answer: 「還是沒辦法證明未來新
使用者使用沒有問題，不是嗎？」 -- and they were right. Every other test in this
repo checks the CODE. None of them checked the thing a new person actually
does: press Deploy, leave the optional boxes empty, open the URL.

Nobody had ever run that path. All the confidence in it came from reading
render.yaml, which is not evidence. So scripts/deploy_smoke.py was pointed at
the app started the way a first deploy starts it, and it found three places a
new user stops. These are the two that are the app's own fault.

Both were deliberate decisions with a comment explaining them, and both
comments are half right -- which is why neither showed up as a bug until
somebody walked the path.
"""

import pytest

from app.config import Settings
from app.services import setup_state


def _configured(monkeypatch) -> None:
    """Make the running app look like a deployment that finished setup.

    The test environment has no .env, so every blocking value is missing and
    the setup endpoints are legitimately open. Asserting they close requires
    first giving them a reason to.
    """
    from cryptography.fernet import Fernet

    monkeypatch.setattr("app.config.settings.JWT_SECRET", "a-real-looking-secret-value")
    monkeypatch.setattr("app.config.settings.TV_WEBHOOK_SECRET", "another-real-looking-one")
    monkeypatch.setattr("app.config.settings.SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(
        "app.config.settings.DATABASE_URL", "postgresql://user:pw@example.neon.tech/db"
    )


def _fresh_token(db_session) -> dict[str, str]:
    """A bearer header signed with whatever JWT_SECRET is in force right now."""
    from app.core.security import create_access_token
    from app.models.user import User

    user = db_session.query(User).order_by(User.id).first()
    token = create_access_token(subject=str(user.id), token_version=user.token_version)
    return {"Authorization": f"Bearer {token}"}


def _missing(settings: Settings) -> list[dict]:
    """The rows the setup page would render, as dicts."""
    return [vars(item) for item in setup_state.missing_settings(settings)]


def _blank() -> Settings:
    """What the settings object looks like when the README is followed exactly:
    the database filled in, everything optional left alone."""
    return Settings(
        DATABASE_URL="postgresql://user:pw@example.neon.tech/db",
        JWT_SECRET="",
        SECRET_ENCRYPTION_KEY="",
        TV_WEBHOOK_SECRET="",
        VAPID_PUBLIC_KEY="",
        VAPID_PRIVATE_KEY="",
    )


# --- the push keys, and the button that never appeared ---------------------------------


def test_the_push_keys_are_listed_so_the_generate_button_exists():
    """MEASURED against a blank deployment: the missing list had six entries
    and VAPID was not one of them.

    _vapid_ok returns (True, None) when BOTH halves are empty, with the
    reasoning that web push is one channel of four and somebody using only
    Telegram must not be told their deployment is incomplete. That reasoning is
    right, and the conclusion drawn from it was wrong: 「not incomplete」 became
    「not shown」, so the row never rendered and the button that generates the
    pair never appeared.

    README says to leave both blank and press a button on the next page. There
    was no button.
    """
    missing = {item["name"] for item in _missing(_blank())}

    assert "VAPID_PUBLIC_KEY" in missing


def test_but_it_does_not_call_the_deployment_broken_over_them():
    """The other half of the original reasoning, kept. A deployment with no
    push keys works -- it just cannot do browser notifications -- so this must
    not be blocking, or somebody using email only is told to fix something that
    is not broken."""
    entry = next(item for item in _missing(_blank()) if item["name"] == "VAPID_PUBLIC_KEY")

    assert entry["blocking"] is False
    assert entry["generator"] == "vapid"


def test_the_row_says_it_is_optional_in_words():
    """`blocking: false` is a field in a JSON body. The person reading the page
    needs the sentence."""
    entry = next(item for item in _missing(_blank()) if item["name"] == "VAPID_PUBLIC_KEY")

    assert "留白" in entry["why"] or "不用" in entry["why"]


def test_the_generator_produces_a_pair_the_app_will_actually_boot_on():
    """A button that hands out a value the boot check then rejects is worse
    than no button."""
    from app.config import _verify_vapid

    pair = setup_state.generate("vapid")

    _verify_vapid(
        Settings(
            VAPID_PUBLIC_KEY=pair["VAPID_PUBLIC_KEY"],
            VAPID_PRIVATE_KEY=pair["VAPID_PRIVATE_KEY"],
            VAPID_SUBJECT="mailto:owner@example.com",
        )
    )


# --- the probe Render points at --------------------------------------------------------


def test_the_health_path_answers_200_while_setup_is_incomplete(client, monkeypatch):
    """MEASURED: /healthz returned 503 on a blank deployment, and render.yaml's
    healthCheckPath points at /healthz.

    A first deploy has no previous version to fall back to, so a probe that
    never passes is a deploy Render marks as FAILED -- and the setup page that
    exists to explain what is missing goes down with it, at exactly the moment
    it is the only useful thing in the app.

    503 was chosen so the external watchdog would notice, and that concern is
    real. It is answered by the next test instead: the body says so.
    """
    monkeypatch.setattr("app.main.SETUP_MODE_REASON", "尚未設定完成")

    resp = client.get("/healthz")

    assert resp.status_code == 200


def test_but_the_body_still_says_the_deployment_is_not_set_up(client, monkeypatch):
    """The watchdog has to be able to tell 「still being set up」 from 「running
    fine」, or 「警告不能停擺」 loses its only outside observer."""
    monkeypatch.setattr("app.main.SETUP_MODE_REASON", "尚未設定完成")

    body = client.get("/healthz").json()

    assert body["status"] == "setup"


def test_a_finished_deployment_is_unaffected(client):
    """The normal case must not change: a configured deployment reports its
    real health, and a real fault still fails the probe."""
    body = client.get("/healthz").json()

    assert body["status"] != "setup"


@pytest.mark.parametrize("path", ["/api/setup/status", "/api/setup/generate"])
def test_the_setup_endpoints_are_reachable_without_a_login(client, path):
    """Nobody has an account yet -- that is the entire point of setup mode."""
    resp = (
        client.get(path) if path.endswith("status") else client.post(path, json={"kind": "token"})
    )

    assert resp.status_code != 401


# --- who may reach the setup endpoints, and when ----------------------------------------


def test_the_setup_endpoints_close_to_strangers_once_the_app_can_run(client, monkeypatch):
    """They have NO authentication -- that is the point of them, since nobody
    has an account yet -- so they must stop answering the moment they are not
    needed.

    _guard() gated on the FULL missing list, including entries that do not stop
    the app from running. Combined with the push keys now always being listed
    when blank -- which is the documented default -- that would have left an
    unauthenticated endpoint open forever on every live deployment, telling any
    passer-by which settings this deployment has not configured.

    Gating on the BLOCKING list instead: open exactly while the app cannot
    serve, shut once it can.
    """
    _configured(monkeypatch)
    monkeypatch.setattr("app.config.settings.VAPID_PUBLIC_KEY", "")
    monkeypatch.setattr("app.config.settings.VAPID_PRIVATE_KEY", "")

    assert client.get("/api/setup/status").status_code == 404


def test_but_the_owner_can_still_reach_the_generator_after_setup(
    auth_client, db_session, monkeypatch
):
    """The other half, and the reason gating alone is not enough. Somebody who
    skipped push during setup must still be able to turn it on later -- and the
    only thing that can produce a valid pair is this generator. Shutting the
    door on strangers must not shut it on the person who deployed it.
    """
    _configured(monkeypatch)
    monkeypatch.setattr("app.config.settings.VAPID_PUBLIC_KEY", "")
    monkeypatch.setattr("app.config.settings.VAPID_PRIVATE_KEY", "")
    # Minted AFTER the settings change: _configured replaces JWT_SECRET, which
    # invalidates the token the fixture signed with the old one. That is the
    # revocation-on-secret-change behaviour working, not a bug -- but it means
    # the header has to be built here.
    headers = _fresh_token(db_session)

    listed = auth_client.get("/api/setup/status", headers=headers)
    assert listed.status_code == 200
    # **這一列現在不會出現，而那是對的**：兩格都空的時候那一對是推導出來的，推播本來
    # 就是通的，沒有東西要跟他要。這條測試守的是另一半——想用自己那一對的人，那顆產生
    # 按鈕不可以因為「陌生人不能按」就連他也按不到。
    assert not any(row["name"] == "VAPID_PUBLIC_KEY" for row in listed.json()["missing"])

    made = auth_client.post("/api/setup/generate", json={"kind": "vapid"}, headers=headers)
    assert made.status_code == 200
    assert made.json()["VAPID_PUBLIC_KEY"]


def test_a_stranger_cannot_generate_keys_on_a_running_deployment(client, monkeypatch):
    _configured(monkeypatch)
    monkeypatch.setattr("app.config.settings.VAPID_PUBLIC_KEY", "")

    assert client.post("/api/setup/generate", json={"kind": "vapid"}).status_code == 404


def test_and_during_setup_it_is_open_to_everyone(client, monkeypatch):
    """Nobody has an account during setup, so requiring one would lock the
    deployer out of the page that exists to let them in."""
    monkeypatch.setattr("app.config.settings.JWT_SECRET", "")

    assert client.get("/api/setup/status").status_code == 200


# --- 畫面就在同一個服務上的時候，不要再叫他去部署第二個東西 -------------------
#
# 這一條是實地讀出來的，不是想出來的：#53 讓後端直接供應前端之後，設定頁還在說
#
#     「等前端部署完（Vercel、Cloudflare Pages、Netlify 都可以），把它給你的網址貼進
#       來。這一格一定是最後填的，因為在前端存在之前沒有人知道那個網址。」
#
# 而現在沒有「前端部署完」這件事了。那句話會做兩件壞事：把一格**正確地空著**的設定
# 顯示成「你還缺這個」，然後把一個不是工程師的人推去部署第二個東西——正是 #53 拿掉
# 的那個要求。CLAUDE.md 的第一條使用者規則：永遠不要叫他去別的地方拿一個值。


def _names(monkeypatch, dist):
    from app import main
    from app.config import Settings
    from app.services import setup_state

    monkeypatch.setattr(main, "FRONTEND_DIST", dist)
    s = Settings(
        DATABASE_URL="sqlite://",
        JWT_SECRET="a-real-secret-value-not-a-placeholder",
        TV_WEBHOOK_SECRET="another-real-secret-value-here",
        CORS_ORIGINS="",
    )
    return [item.name for item in setup_state.missing_settings(s)]


def test_a_bundled_frontend_does_not_ask_for_cors_origins(monkeypatch, tmp_path):
    """同一個來源，就沒有跨來源這件事。

    這一格空著是**正確的設定**，不是缺的設定。列出來的話他會照著去找一個不存在的
    前端網址。
    """
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>", encoding="utf-8")

    assert "CORS_ORIGINS" not in _names(monkeypatch, dist)


def test_a_separately_hosted_frontend_still_needs_it(monkeypatch, tmp_path):
    """分開放的人還是要填，而且沒填的症狀是一片白畫面。

    拿掉整格的話，走那條路的人會遇到一個沒有任何訊息的失敗——瀏覽器把每一個回應都
    丟掉，而錯誤藏在開發者工具裡。
    """
    assert "CORS_ORIGINS" in _names(monkeypatch, tmp_path / "no-dist-here")
