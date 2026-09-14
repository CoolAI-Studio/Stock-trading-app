"""TradingView 的網址本身就是憑證：每個帳號一條，app 產生、直接複製就能用（#115）。

＊ 為什麼。

ONBOARDING.md 方案 4 的規格是「系統給他 webhook 網址＋密碼，**都可以直接複製，不用去別的
地方拿**」。原本的設定面板刻意不印出真正的 TV_WEBHOOK_SECRET——理由是對的，它會進瀏覽器快
取和每一張截圖——結果是訊息範本裡寫著 `<你的 TV_WEBHOOK_SECRET>`，而他只能去部署平台的環
境變數裡翻。那正是 CLAUDE.md 寫給這個使用者的第一條規則：永遠不要叫他去別的地方拿一個值。

而 `_resolve_user` 的註解早就寫著真正的解法：每個帳號自己的網址。共用密碼只證明「這是這
個部署的某個人」，所以一有兩個帳號，就只能拒絕（#24）。

＊ 形狀。

    /api/webhooks/tradingview/{user_id}.{version}.{mac}

mac 是 TV_WEBHOOK_SECRET 對 (user_id, version) 算的 HMAC-SHA256。每一條性質底下都有一個
測試：

1. **驗證 MAC 不需要資料庫**——所以一個亂打的網址叫不醒 Neon。這跟共用密碼那條「密碼對了才
   寫」是同一條原則，而這支端點是公開的、誰都打得到。
2. **帳號由 MAC 證明，不用猜**——兩個帳號的部署照樣歸屬正確，而且改掉網址裡的帳號編號不能
   冒充別人。
3. **不存任何新的秘密**：網址是算出來的，資料庫裡只有一個版本號。重新產生＝版本號加一，舊
   網址立刻失效——但它還被呼叫時要寫進收件紀錄，否則他換了網址卻忘了改 TradingView，症狀
   是「提醒不響了」而畫面上什麼都沒有。
4. **網址是憑證，所以要當憑證對待**：設定回應不進快取；uvicorn 的 access log 會印完整路徑，
   要遮掉。
"""

import json
import logging
from urllib.parse import urlsplit

import pytest

from app.models.order import Order
from app.models.user import User
from app.models.webhook import TradingViewWebhookLog

TV_SECRET = "a-deployment-secret-that-is-long-enough-for-hmac"


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setattr("app.config.settings.TV_WEBHOOK_SECRET", TV_SECRET)


def _alert(**overrides) -> str:
    body = {"symbol": "AAPL", "action": "buy", "quantity": 1, "id": "alert-1"}
    body.update(overrides)
    return json.dumps(body)


def _personal_path(client, headers=None) -> str:
    resp = client.get("/api/webhooks/tradingview/setup", headers=headers or {})
    assert resp.status_code == 200, resp.text
    return urlsplit(resp.json()["url"]).path


def _post(client, path: str, body: str):
    return client.post(path, content=body, headers={"Content-Type": "text/plain"})


def _owner(db_session) -> User:
    return db_session.query(User).filter(User.email == "fixture-user@example.com").one()


# --- 他拿到的東西 ----------------------------------------------------------------


def test_the_setup_hands_out_a_url_that_needs_no_other_value(auth_client, secret):
    resp = auth_client.get("/api/webhooks/tradingview/setup")

    assert resp.status_code == 200, resp.text
    setup = resp.json()
    path = urlsplit(setup["url"]).path
    assert path.startswith("/api/webhooks/tradingview/")
    assert path != "/api/webhooks/tradingview", "還是那條要密碼的共用網址"
    # 範本裡不可以再出現一個要他去別的地方拿的值。
    assert '"secret"' not in setup["example_message"]
    assert "TV_WEBHOOK_SECRET" not in setup["example_message"]


def test_the_setup_response_is_not_cached(auth_client, secret):
    """網址就是憑證。一個被瀏覽器或中間的代理存下來的回應，是一份沒有人管的副本。"""
    resp = auth_client.get("/api/webhooks/tradingview/setup")

    assert "no-store" in resp.headers.get("cache-control", "")


# --- 貼上去之後 ------------------------------------------------------------------


def test_an_alert_posted_to_that_url_becomes_his_signal(auth_client, db_session, secret):
    path = _personal_path(auth_client)

    resp = _post(auth_client, path, _alert())

    assert resp.status_code == 202, resp.text
    assert resp.json()["ok"] is True
    order = db_session.query(Order).filter(Order.symbol == "AAPL").one()
    assert order.user_id == _owner(db_session).id
    assert order.source == "tradingview"


def test_a_pasted_secret_is_never_copied_into_the_log(auth_client, db_session, secret):
    """他可能照舊的範本把 secret 那一行也貼過來。那是共用密碼，不可以因為換了網址就被存成明文。"""
    path = _personal_path(auth_client)

    _post(auth_client, path, _alert(secret=TV_SECRET))

    log = db_session.query(TradingViewWebhookLog).one()
    assert TV_SECRET not in log.raw_body
    assert "secret" not in json.loads(log.raw_body)


# --- 陌生人 ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "token",
    [
        "1.0." + "A" * 43,  # 形狀對、MAC 錯
        "not-a-token",
        "1.0",
        "x.y.z",
        "1.-1." + "A" * 43,
    ],
)
def test_a_guessed_url_is_refused_without_touching_the_database(client, counted, secret, token):
    """這支端點是公開的。驗證要在任何一句 SQL 之前完成，否則一個迴圈就能把 Neon 釘在醒著。"""
    counted.clear()

    resp = _post(client, f"/api/webhooks/tradingview/{token}", _alert())

    assert resp.status_code == 401
    assert counted == [], f"一個猜錯的網址送出了 {len(counted)} 句 SQL"


def test_one_accounts_url_cannot_be_edited_into_another_account(
    auth_client, second_user_headers, db_session, secret
):
    owner_path = _personal_path(auth_client)
    second = db_session.query(User).filter(User.email == "second-account@example.com").one()
    _, _, mac = owner_path.rsplit("/", 1)[1].split(".")

    resp = _post(auth_client, f"/api/webhooks/tradingview/{second.id}.0.{mac}", _alert())

    assert resp.status_code == 401
    assert db_session.query(Order).count() == 0


def test_with_two_accounts_each_url_still_lands_in_its_own_ledger(
    auth_client, second_user_headers, db_session, secret
):
    """共用密碼在這裡只能拒絕（#24）。每個帳號自己的網址不用猜。"""
    owner_path = _personal_path(auth_client)
    second_path = _personal_path(auth_client, headers=second_user_headers)
    second = db_session.query(User).filter(User.email == "second-account@example.com").one()

    assert owner_path != second_path
    _post(auth_client, owner_path, _alert(symbol="AAPL", id="owner-1"))
    _post(auth_client, second_path, _alert(symbol="2330.TW", id="second-1"))

    assert db_session.query(Order).filter(Order.symbol == "AAPL").one().user_id == (
        _owner(db_session).id
    )
    assert db_session.query(Order).filter(Order.symbol == "2330.TW").one().user_id == second.id


# --- 重新產生 --------------------------------------------------------------------


def test_regenerating_retires_the_old_url_and_the_log_says_so(auth_client, db_session, secret):
    old_path = _personal_path(auth_client)

    rotated = auth_client.post("/api/webhooks/tradingview/setup/rotate")
    assert rotated.status_code == 200, rotated.text
    assert "no-store" in rotated.headers.get("cache-control", "")
    new_path = urlsplit(rotated.json()["url"]).path
    assert new_path != old_path

    stale = _post(auth_client, old_path, _alert(id="after-rotate"))
    # 2xx，因為 TradingView 會重送任何非 2xx，而這一則重送一萬次也不會成功。
    assert stale.status_code == 200
    assert stale.json()["ok"] is False
    assert db_session.query(Order).count() == 0
    # 他換了網址卻忘了改 TradingView：症狀是「不響了」。收件紀錄是唯一說得出原因的地方。
    log = db_session.query(TradingViewWebhookLog).one()
    assert log.user_id == _owner(db_session).id
    assert "重新產生" in (log.error or "")

    fresh = _post(auth_client, new_path, _alert(id="after-rotate-new"))
    assert fresh.status_code == 202, fresh.text


def test_changing_the_deployment_secret_retires_every_url(
    auth_client, db_session, monkeypatch, secret
):
    """網址是從 TV_WEBHOOK_SECRET 算出來的。換掉它就是「所有 TradingView 網址都作廢」，
    跟換掉共用密碼的意思一樣——這是刻意的，所以要被釘住，不是被發現。"""
    path = _personal_path(auth_client)
    monkeypatch.setattr("app.config.settings.TV_WEBHOOK_SECRET", "a-completely-different-secret")

    resp = _post(auth_client, path, _alert())

    assert resp.status_code == 401
    assert db_session.query(Order).count() == 0


# --- 網址是憑證，所以不可以被印出來 -----------------------------------------------


def _access_record(path: str) -> logging.LogRecord:
    """uvicorn.protocols.http 寫 access log 的樣子：訊息是格式字串，路徑在 args 裡。"""
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("203.0.113.9:443", "POST", path, "1.1", 202),
        None,
    )


def test_the_access_log_does_not_print_the_url(auth_client, secret):
    import app.main  # noqa: F401 -- 過濾器是在 app 載入時裝上的

    path = _personal_path(auth_client)
    token = path.rsplit("/", 1)[1]
    record = _access_record(path)

    for installed in logging.getLogger("uvicorn.access").filters:
        (installed.filter if hasattr(installed, "filter") else installed)(record)

    line = record.getMessage()
    assert token not in line
    assert "/api/webhooks/tradingview/" in line, "整條路徑都拿掉的話，log 就看不出是誰在打"


def test_other_paths_are_left_alone(auth_client):
    import app.main  # noqa: F401

    record = _access_record("/api/strategies?limit=5")
    for installed in logging.getLogger("uvicorn.access").filters:
        (installed.filter if hasattr(installed, "filter") else installed)(record)

    assert "/api/strategies?limit=5" in record.getMessage()
