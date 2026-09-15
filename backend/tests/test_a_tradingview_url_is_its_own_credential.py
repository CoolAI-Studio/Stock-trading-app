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

mac 是對 (user_id, version) 算的 HMAC-SHA256，金鑰從 SECRET_ENCRYPTION_KEY 推導——**不是**
TV_WEBHOOK_SECRET，那一個明文寫在每一則舊警報的訊息裡（理由在 tradingview_url 的檔頭第 4 條）。
每一條性質底下都有一個測試：

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


def test_a_leaked_shared_secret_cannot_mint_a_personal_url(auth_client, db_session, secret):
    """共用密碼會明文寫在每一則舊警報的「訊息」裡：TradingView 的彈窗、通知信、手機通知、截圖都
    帶著它。網址如果是從它算出來的，拿到它的人就能替任何帳號、任何版本算出一條——「重新產生」
    擋不住（一次唯讀審查抓到的）。"""
    from app.services import tradingview_url

    owner = _owner(db_session)
    forged = tradingview_url.token_for(owner.id, owner.webhook_url_version, TV_SECRET)

    resp = _post(auth_client, f"/api/webhooks/tradingview/{forged}", _alert())

    assert resp.status_code == 401
    assert db_session.query(Order).count() == 0


def test_changing_the_shared_secret_leaves_personal_urls_working(
    auth_client, db_session, monkeypatch, secret
):
    """共用密碼外洩時的補救是換掉 TV_WEBHOOK_SECRET。那一步只該讓舊的共用網址失效，不可以連帶
    弄斷他已經換好、貼在每一則警報裡的專屬網址——那會是一次沒有人看得到的全面停擺。"""
    path = _personal_path(auth_client)
    monkeypatch.setattr("app.config.settings.TV_WEBHOOK_SECRET", "a-completely-different-secret")

    resp = _post(auth_client, path, _alert())

    assert resp.status_code == 202, resp.text


def test_changing_the_encryption_key_retires_every_url(
    auth_client, db_session, monkeypatch, secret
):
    """網址是從 SECRET_ENCRYPTION_KEY 推出來的。換掉它就是所有 TradingView 網址一起作廢——跟它
    讓加密過的通知設定全部解不開是同一件事，所以要被釘住，不是被發現。"""
    from cryptography.fernet import Fernet

    path = _personal_path(auth_client)
    monkeypatch.setattr("app.config.settings.SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode())

    resp = _post(auth_client, path, _alert())

    assert resp.status_code == 401
    assert db_session.query(Order).count() == 0


def test_the_setup_says_what_to_do_when_the_shared_secret_leaks(auth_client, secret):
    """「重新產生」擋得住外洩的網址，擋不住外洩的共用密碼。他要知道後者的補救是哪一個動作。"""
    notes = " ".join(auth_client.get("/api/webhooks/tradingview/setup").json()["notes"])

    assert "外洩" in notes
    assert "TV_WEBHOOK_SECRET" in notes


def test_a_retired_url_hammered_in_a_loop_stops_costing_the_database(
    auth_client, db_session, counted, secret
):
    """外洩了、他照指示按了重新產生——而拿到舊網址的人繼續用迴圈打它。

    舊網址的 MAC 是真的，所以它一路走到資料庫：查帳號、寫一列、修剪紀錄，每一次大約六句。
    沒有限流的話，這正是「一個迴圈把 Neon 釘在醒著」，而重新產生原本是他唯一的補救。
    版本號只會往上加，所以「這一條已經退休」一旦知道就永遠是真的——記在行程裡，不用再問。
    """
    old_path = _personal_path(auth_client)
    auth_client.post("/api/webhooks/tradingview/setup/rotate")
    _post(auth_client, old_path, _alert(id="stale-0"))
    counted.clear()

    for n in range(1, 6):
        resp = _post(auth_client, old_path, _alert(id=f"stale-{n}"))
        assert resp.status_code == 200
        assert resp.json()["ok"] is False
        assert "重新產生" in resp.json()["error"]

    assert counted == [], f"退休的網址被連打五次，送出了 {len(counted)} 句 SQL"
    assert db_session.query(TradingViewWebhookLog).count() == 1


def test_but_the_log_still_hears_about_it_again_later(auth_client, db_session, monkeypatch, secret):
    """省的是連打的那一串，不是提醒他：隔一段時間還在打，收件紀錄要再記一次。"""
    from app.api.routers import webhooks

    now = [1000.0]
    monkeypatch.setattr(webhooks, "_clock", lambda: now[0])
    old_path = _personal_path(auth_client)
    auth_client.post("/api/webhooks/tradingview/setup/rotate")

    _post(auth_client, old_path, _alert(id="stale-a"))
    now[0] += webhooks._RETIRED_URL_LOG_EVERY_SEC + 1
    _post(auth_client, old_path, _alert(id="stale-b"))

    assert db_session.query(TradingViewWebhookLog).count() == 2


def test_a_retired_url_cannot_make_a_real_alert_look_like_a_replay(auth_client, db_session, secret):
    """沒有 id 的警報靠「短時間內一模一樣的內容」擋重放，而退休網址寫下的那一列原本也算數。

    拿到舊網址的人先送一段內容，他新網址上一模一樣的真警報就會被當成重放略過——提醒沒了，
    收件紀錄還說是它自己重複。
    """
    old_path = _personal_path(auth_client)
    rotated = auth_client.post("/api/webhooks/tradingview/setup/rotate")
    new_path = urlsplit(rotated.json()["url"]).path
    body = json.dumps({"symbol": "AAPL", "action": "buy", "quantity": 1})

    _post(auth_client, old_path, body)
    real = _post(auth_client, new_path, body)

    assert real.status_code == 202, real.text
    assert real.json()["created"] is True
    assert db_session.query(Order).count() == 1


# --- 同一秒響的兩則 ----------------------------------------------------------------


def test_two_alerts_that_fire_in_the_same_second_are_both_signals(auth_client, db_session, secret):
    """範本的 id 是 {{timenow}}，而 TradingView 給它的精度只到秒（2023-06-01T17:38:10Z）。

    兩檔股票的收盤警報在同一秒響是日常，不是巧合。原本 id 原封不動當成去重的鍵，於是第二則
    回「duplicate idempotency_key」：沒有訊號、沒有通知，收件紀錄還掛著第一則的訂單編號。
    改在伺服器這邊把代號和買賣算進去，已經設好的警報不用改就得救。
    """
    path = _personal_path(auth_client)
    same_second = "2026-09-14T20:00:00Z"

    first = _post(auth_client, path, _alert(symbol="AAPL", id=same_second))
    second = _post(auth_client, path, _alert(symbol="NVDA", id=same_second))

    assert first.json()["created"] is True
    assert second.json()["created"] is True, second.text
    assert {order.symbol for order in db_session.query(Order).all()} == {"AAPL", "NVDA"}


def test_but_the_same_alert_delivered_twice_is_still_one_signal(auth_client, db_session, secret):
    """TradingView 重送的是同一段內容。那一則還是只能算一次。"""
    path = _personal_path(auth_client)
    body = _alert(symbol="AAPL", id="2026-09-14T20:00:00Z")

    _post(auth_client, path, body)
    again = _post(auth_client, path, body)

    assert again.json()["created"] is False
    assert db_session.query(Order).count() == 1


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
