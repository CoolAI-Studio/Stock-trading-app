"""舊的共用網址可以關掉（#115 的後續）。

＊ 為什麼要能關。

舊的設定方式把 TV_WEBHOOK_SECRET 明文寫在每一則警報的「訊息」裡，而那段訊息會跟著
TradingView 的彈窗、通知信、手機通知、截圖一起流出去。專屬網址讓訊息裡不再需要密碼，但
只要共用網址還開著，外洩的舊密碼就還能替他送訊號。等他把每一則警報都換成專屬網址，把共
用網址關掉，那個密碼就完全沒用了。

＊ 為什麼不能自動關。

「更新不可以停掉已經在跑的那一份」（#50）：他三個月前設好、早就忘了的警報，還打在共用網
址上。自動關掉就是那些警報從某一天起不再響，而畫面上什麼都沒變。所以開關是他按的，旁邊
寫著共用網址最後一次收到訊號是什麼時候——他看得到「還有沒有東西在用它」再決定。

＊ 釘在這裡的：

- 關掉之後帶密碼的警報不再變成訊號，而且收件紀錄說得出為什麼（不然就是「不響了」）。
- 關掉的門被連打不叫醒資料庫——外洩的密碼正是他按下去的原因。
- 重新打開立刻生效，不可以因為行程裡記著「關了」而多擋一段時間：那段時間的警報就沒了。
- 密碼錯的請求照舊在碰資料庫之前擋掉。
- 專屬網址不受影響。
"""

import json
from urllib.parse import urlsplit

import pytest

from app.models.order import Order
from app.models.webhook import TradingViewWebhookLog

TV_SECRET = "a-deployment-secret-that-is-long-enough-for-hmac"
SHARED = "/api/webhooks/tradingview/shared"


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setattr("app.config.settings.TV_WEBHOOK_SECRET", TV_SECRET)


def _shared_door(client, **overrides):
    body = {"secret": TV_SECRET, "symbol": "AAPL", "action": "buy", "quantity": 1, "id": "shared-1"}
    body.update(overrides)
    return client.post(
        "/api/webhooks/tradingview",
        content=json.dumps(body),
        headers={"Content-Type": "text/plain"},
    )


def _close(client):
    resp = client.put(SHARED, json={"enabled": False})
    assert resp.status_code == 200, resp.text
    assert resp.json()["enabled"] is False


def test_it_needs_a_login(client):
    assert client.get(SHARED).status_code == 401
    assert client.put(SHARED, json={"enabled": False}).status_code == 401


def test_it_starts_open_and_unused(auth_client, secret):
    assert auth_client.get(SHARED).json() == {"enabled": True, "last_used_at": None}


def test_a_call_on_the_shared_door_is_remembered(auth_client, secret):
    """「最後一次收到」是他決定能不能關的唯一依據。"""
    assert _shared_door(auth_client).status_code == 202

    assert auth_client.get(SHARED).json()["last_used_at"] is not None


def test_closed_means_an_alert_carrying_the_secret_is_no_longer_a_signal(
    auth_client, db_session, secret
):
    _close(auth_client)

    resp = _shared_door(auth_client)

    # 200，因為 TradingView 會重送任何非 2xx，而這一則重送一萬次也不會成功。
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert "關掉" in resp.json()["error"]
    assert db_session.query(Order).count() == 0
    # 忘了換網址的那則警報：症狀是「不響了」，收件紀錄是唯一說得出原因的地方。
    log = db_session.query(TradingViewWebhookLog).one()
    assert "關掉" in (log.error or "")
    assert TV_SECRET not in log.raw_body


def test_a_closed_door_hammered_in_a_loop_costs_the_database_nothing(
    auth_client, db_session, counted, secret
):
    """外洩的密碼正是他關門的原因，而拿到它的人可以繼續用迴圈打。"""
    _close(auth_client)
    _shared_door(auth_client, id="first")
    counted.clear()

    for n in range(5):
        resp = _shared_door(auth_client, id=f"again-{n}")
        assert resp.status_code == 200
        assert resp.json()["ok"] is False

    assert counted == [], f"關掉的共用網址被連打五次，送出了 {len(counted)} 句 SQL"
    assert db_session.query(TradingViewWebhookLog).count() == 1


def test_reopening_works_at_once(auth_client, db_session, secret):
    """行程裡記著「關了」是為了省資料庫，不可以讓重新打開晚一段時間才生效。"""
    _close(auth_client)
    _shared_door(auth_client, id="while-closed")

    reopened = auth_client.put(SHARED, json={"enabled": True})
    assert reopened.json()["enabled"] is True
    resp = _shared_door(auth_client, id="after-reopening")

    assert resp.status_code == 202, resp.text
    assert resp.json()["created"] is True


def test_closing_it_leaves_the_personal_url_alone(auth_client, db_session, secret):
    _close(auth_client)
    setup = auth_client.get("/api/webhooks/tradingview/setup").json()
    path = urlsplit(setup["url"]).path
    body = json.dumps({"symbol": "AAPL", "action": "buy", "quantity": 1, "id": "personal-1"})

    resp = auth_client.post(path, content=body, headers={"Content-Type": "text/plain"})

    assert resp.status_code == 202, resp.text
    assert db_session.query(Order).count() == 1


def test_a_wrong_secret_is_still_refused_before_the_database(auth_client, counted, secret):
    """開關要在密碼驗過之後才看。先看開關就是讓陌生人免費叫醒資料庫。"""
    _close(auth_client)
    counted.clear()

    resp = _shared_door(auth_client, secret="not-the-secret")

    assert resp.status_code == 401
    assert counted == []


def test_the_setup_notes_follow_the_switch(auth_client, secret):
    """說明原本寫著「舊的警報照樣有效」。關掉之後那句話是錯的。"""
    open_notes = " ".join(auth_client.get("/api/webhooks/tradingview/setup").json()["notes"])
    _close(auth_client)
    closed_notes = " ".join(auth_client.get("/api/webhooks/tradingview/setup").json()["notes"])

    assert "照樣有效" in open_notes
    assert "照樣有效" not in closed_notes
    assert "已經關掉" in closed_notes
