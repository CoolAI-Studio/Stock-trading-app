"""收盤摘要的開關（#117）：他看得到它會摘要哪幾檔、上一次送出是什麼時候。

摘要的清單就是自選股（ONBOARDING.md 通則 6：引導做出來的東西是普通資料），所以這支端點
不收代號，只收「開／關」。要加哪一檔，用自選股那支本來就在驗代號的端點。

畫面要說得出三件事，否則開關打開之後他無從判斷有沒有用：
- 每個市場會摘要哪幾檔（台股收盤一則、美股收盤一則）
- 哪幾檔**不會**進摘要（加密貨幣不收盤）
- 上一次送出是什麼時候、最後一次出了什麼問題
"""

from app.enums import DataSource
from app.models.user import User
from app.models.watchlist import WatchlistItem


def _owner(db_session) -> User:
    return db_session.query(User).filter(User.email == "fixture-user@example.com").one()


def _watch(db_session, user: User, symbol: str, source: DataSource = DataSource.YFINANCE):
    db_session.add(WatchlistItem(user_id=user.id, symbol=symbol, data_source=source))
    db_session.commit()


def test_it_needs_a_login(client):
    assert client.get("/api/daily-summary").status_code == 401
    assert client.put("/api/daily-summary", json={"is_enabled": True}).status_code == 401


def test_it_starts_switched_off_and_says_what_it_would_cover(auth_client, db_session):
    owner = _owner(db_session)
    _watch(db_session, owner, "2330.TW")
    _watch(db_session, owner, "AAPL")
    _watch(db_session, owner, "BTCUSDT", DataSource.BINANCE)

    body = auth_client.get("/api/daily-summary").json()

    assert body["is_enabled"] is False
    by_market = {market["market"]: market for market in body["markets"]}
    assert by_market["tw"]["symbols"] == ["2330.TW"]
    assert by_market["us"]["symbols"] == ["AAPL"]
    assert by_market["tw"]["label"] == "台股"
    assert body["unsupported"] == ["BTCUSDT"]
    assert body["last_sent_at"] is None


def test_switching_it_on_is_remembered(auth_client):
    put = auth_client.put("/api/daily-summary", json={"is_enabled": True})
    assert put.status_code == 200, put.text
    assert put.json()["is_enabled"] is True

    assert auth_client.get("/api/daily-summary").json()["is_enabled"] is True

    auth_client.put("/api/daily-summary", json={"is_enabled": False})
    assert auth_client.get("/api/daily-summary").json()["is_enabled"] is False


def test_another_account_sees_only_its_own(auth_client, second_user_headers, db_session):
    owner = _owner(db_session)
    _watch(db_session, owner, "2330.TW")
    auth_client.put("/api/daily-summary", json={"is_enabled": True})

    theirs = auth_client.get("/api/daily-summary", headers=second_user_headers).json()

    assert theirs["is_enabled"] is False
    assert all(market["symbols"] == [] for market in theirs["markets"])
