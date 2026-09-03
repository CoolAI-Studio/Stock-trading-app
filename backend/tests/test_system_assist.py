"""「我為什麼收不到通知」, answered against this deployment rather than in general.

The owner asked for a template a stranger can use quickly, with AI helping
wherever they do not understand something. There is already an assistant in the
app (broker credentials), and a strategy generator. What was missing is the
question a non-developer actually asks, which is never 「how do I format an API
key」 -- it is 「something is wrong and I do not know what」.

A general chatbot answers that badly, because the useful answer depends on
facts only this process has: whether the worker is running, which symbol has
not priced for an hour, whether NOTIFICATIONS_ENABLED got switched off. So the
question is sent with a summary of the CURRENT state attached.

TWO THINGS THIS MUST NOT DO.

  Depend on itself. AI needs AI_API_KEY, which is one more blank in a deploy
  form -- so the setup flow can never require the assistant, and the assistant
  must report 「not available」 cleanly rather than breaking a page when the key
  is unset. Setup is explained by the setup page; this is for afterwards.

  Send anything secret. The context is counts, ages, booleans and the NAMES of
  settings that are missing -- never a value of any of them, and never a token,
  a key or a password. It goes to a third party, so what goes has to be
  something the owner would be comfortable reading out loud.
"""

from unittest.mock import patch

from app.services.ai_provider import AIResult


def _reply(text: str = "看起來是 worker 停了。"):
    return patch(
        "app.api.routers.system.get_ai_provider",
        return_value=type(
            "_P", (), {"ask": staticmethod(lambda *a, **k: AIResult(ok=True, reply=text))}
        )(),
    )


def _captured() -> dict:
    """Patches the provider and records what it was actually asked."""
    seen: dict = {}

    class _Provider:
        @staticmethod
        def ask(message: str, system: str | None = None) -> AIResult:
            seen["message"] = message
            seen["system"] = system
            return AIResult(ok=True, reply="ok")

    seen["patch"] = patch("app.api.routers.system.get_ai_provider", return_value=_Provider())
    return seen


# --- who may ask ---------------------------------------------------------------


def test_it_needs_a_login(client):
    resp = client.post("/api/system/assist", json={"message": "為什麼收不到通知"})

    assert resp.status_code == 401


# --- the answer carries this deployment's own facts ---------------------------


def test_the_question_is_sent_with_the_current_state_attached(auth_client):
    """A general chatbot answers 「為什麼收不到通知」 with a checklist. The useful
    answer depends on facts only this process has."""
    seen = _captured()
    with seen["patch"]:
        auth_client.post("/api/system/assist", json={"message": "為什麼收不到通知"})

    assert "為什麼收不到通知" in seen["message"]
    assert "worker" in seen["message"].lower()


def test_the_state_includes_the_symbols_that_are_not_pricing(auth_client, db_session, monkeypatch):
    from decimal import Decimal

    from app.enums import DataSource
    from app.models.strategy import Strategy
    from app.models.user import User
    from app.services import worker_health

    # 掛在呼叫者名下。送進 AI 的那份摘要跟畫面讀的是同一個欄位，而那個欄位現在
    # 只列自己的代號——否則等於換一條路把別人的持股清單送到一個外部服務。
    owner = db_session.query(User).filter(User.email == "fixture-user@example.com").one()
    db_session.add(
        Strategy(
            user_id=owner.id,
            name="watches-2330",
            symbol="2330.TW",
            data_source=DataSource.YFINANCE,
            source_code="class Strategy:" + chr(10) + "    pass" + chr(10),
            code_hash="hash-assist-test",
            default_quantity=Decimal(1),
        )
    )
    db_session.commit()

    class _Beat:
        @staticmethod
        def snapshot():
            return worker_health.HeartbeatSnapshot(
                uptime_sec=100.0,
                last_loop_age_sec=1.0,
                last_poll_age_sec=1.0,
                consecutive_empty_polls=0,
                symbol_gap_sec={"2330.TW": 3600.0},
            )

    monkeypatch.setattr(worker_health, "heartbeat", _Beat())
    seen = _captured()
    with seen["patch"]:
        auth_client.post("/api/system/assist", json={"message": "怎麼回事"})

    assert "2330.TW" in seen["message"]


def test_the_assistant_is_told_what_it_is_for(auth_client):
    """Without its own system prompt it inherits the broker-credential one and
    answers every question as though it were about API keys."""
    seen = _captured()
    with seen["patch"]:
        auth_client.post("/api/system/assist", json={"message": "怎麼回事"})

    assert seen["system"], "no system prompt was supplied"


# --- and nothing secret goes with it -------------------------------------------


def test_no_secret_value_is_ever_sent(auth_client, monkeypatch):
    """It goes to a third party. What goes has to be something the owner would
    be comfortable reading out loud.

    The live values are read rather than substituted: patching JWT_SECRET
    would invalidate the token auth_client is already holding, so the request
    would 401 and the assertion would pass having tested nothing.
    """
    from app.config import settings

    monkeypatch.setattr("app.config.settings.TV_WEBHOOK_SECRET", "super-secret-webhook-value")

    seen = _captured()
    with seen["patch"]:
        auth_client.post("/api/system/assist", json={"message": "怎麼回事"})

    blob = f"{seen['message']}{seen['system']}"
    assert "super-secret-webhook-value" not in blob
    for secret in (settings.JWT_SECRET, settings.SECRET_ENCRYPTION_KEY):
        assert secret and secret not in blob, secret


def test_a_missing_setting_is_named_but_not_valued(auth_client, monkeypatch):
    """The NAME is what makes the advice actionable; the value would be the
    deployment."""
    monkeypatch.setattr("app.config.settings.SECRET_ENCRYPTION_KEY", "")

    seen = _captured()
    with seen["patch"]:
        auth_client.post("/api/system/assist", json={"message": "怎麼回事"})

    assert "SECRET_ENCRYPTION_KEY" in seen["message"]


# --- when there is no AI configured --------------------------------------------


def test_an_unconfigured_assistant_says_so_rather_than_breaking(auth_client, monkeypatch):
    """AI_API_KEY is one more blank in a deploy form, so it is optional by
    design. A page that breaks when it is unset would make the optional thing
    mandatory."""
    monkeypatch.setattr("app.config.settings.AI_API_KEY", "")

    body = auth_client.post("/api/system/assist", json={"message": "怎麼回事"}).json()

    assert body["ok"] is False
    assert "AI_API_KEY" in (body["error"] or "")


def test_the_status_page_can_tell_whether_the_assistant_exists(auth_client, monkeypatch):
    """So the UI can leave the box out entirely rather than offering a feature
    that answers every question with an error."""
    monkeypatch.setattr("app.config.settings.AI_API_KEY", "")

    assert auth_client.get("/api/system/status").json()["assistant_available"] is False


def test_a_configured_assistant_is_advertised(auth_client, monkeypatch):
    monkeypatch.setattr("app.config.settings.AI_API_KEY", "sk-something")
    monkeypatch.setattr("app.config.settings.AI_MODEL", "some-model")

    assert auth_client.get("/api/system/status").json()["assistant_available"] is True


# --- the reply reaches the caller ------------------------------------------------


def test_the_reply_comes_back(auth_client):
    with _reply("worker 停了，去 Render 按 Manual Deploy。"):
        body = auth_client.post("/api/system/assist", json={"message": "怎麼回事"}).json()

    assert body["ok"] is True
    assert "Manual Deploy" in body["reply"]


def test_an_empty_question_is_refused(auth_client):
    resp = auth_client.post("/api/system/assist", json={"message": "   "})

    assert resp.status_code == 422


def test_the_assistant_is_not_taught_to_say_render_to_everyone():
    """助手的提示詞不可以把某一家平台當成範例講法。

    原本那句是「例如『去 Render 按 Manual Deploy』」——那是在教模型用 Render 的說法回
    答**每一個**使用者。而安裝頁給了四條路：Render、Railway、Fly.io、自己的機器。

    這是這個 repo 已經立過的規則，只是漏了這一處：`/api/system/status` 會回
    `platform.env_where`，用他實際所在平台的說法講話，而 system.py 自己的註解就寫著
    「對 Fly.io 的使用者說『Render 後台』比含糊更糟——他會真的去找那一頁」。

    而這一處比別處嚴重：助手正是他搞不懂的時候去問的東西。給他一個不存在的選單路徑，
    等於在他最需要幫忙的那一刻把他推得更遠。
    """
    from app.api.routers.system import _ASSISTANT_PROMPT

    for host in ("Render", "Railway", "Fly.io", "Vercel"):
        assert host not in _ASSISTANT_PROMPT, (
            f"提示詞裡把 {host} 當成範例講法，那會讓助手對每一個使用者都這樣講。"
            "平台名稱要從狀態裡讀，不是寫死在提示詞裡。"
        )


def test_the_assistant_is_told_which_platform_this_is(auth_client, db_session):
    """而且要把平台**告訴**它，不然它只能含糊。

    拿掉範例還不夠：模型手上要有那個名字，才講得出他照著做得到的步驟。這一段本來就在
    /api/system/status 的回應裡（platform.env_where），只是沒有帶進助手看的那份狀態。
    """
    from app.api.routers.system import _state_for_assistant
    from app.models.user import User

    user = db_session.query(User).filter(User.email == "fixture-user@example.com").one()
    summary = _state_for_assistant(db_session, user)

    assert "平台" in summary or "部署在" in summary
