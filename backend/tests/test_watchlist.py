"""The watch list belongs to the person, not to the browser.

It was the only data in the app kept in localStorage. The dashboard's quote
table is the first thing the owner looks at every morning, and it was empty on
their phone, empty on a second computer, and gone entirely after clearing
browsing data -- with nothing on screen ever saying it only existed on that
one machine.
"""

from app.models.enums import DataSource
from app.models.user import User


def _other_user(db_session) -> User:
    from app.core.security import hash_password

    user = User(email="someone-else@example.com", hashed_password=hash_password("pw12345678"))
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def test_a_symbol_can_be_watched_and_comes_back(auth_client):
    assert auth_client.get("/api/watchlist").json() == []

    resp = auth_client.post("/api/watchlist", json={"symbol": "2330.TW"})
    assert resp.status_code == 201, resp.text

    body = auth_client.get("/api/watchlist").json()
    assert [row["symbol"] for row in body] == ["2330.TW"]


def test_symbols_are_stored_uppercase_because_the_providers_expect_it(auth_client):
    auth_client.post("/api/watchlist", json={"symbol": "2330.tw"})
    assert auth_client.get("/api/watchlist").json()[0]["symbol"] == "2330.TW"


def test_watching_the_same_symbol_twice_is_not_an_error(auth_client):
    """Pressing add on something already there should leave the list as it is,
    not fail. Nothing about the owner's intent was wrong."""
    auth_client.post("/api/watchlist", json={"symbol": "AAPL"})
    second = auth_client.post("/api/watchlist", json={"symbol": "AAPL"})

    assert second.status_code in (200, 201)
    assert len(auth_client.get("/api/watchlist").json()) == 1


def test_a_symbol_can_be_dropped(auth_client):
    auth_client.post("/api/watchlist", json={"symbol": "AAPL"})
    assert auth_client.delete("/api/watchlist/AAPL").status_code == 204
    assert auth_client.get("/api/watchlist").json() == []


def test_dropping_something_not_watched_is_a_404(auth_client):
    assert auth_client.delete("/api/watchlist/NOPE").status_code == 404


def test_the_data_source_travels_with_the_symbol(auth_client):
    """BTCUSDT priced off yfinance comes back empty. The list has to remember
    which feed each symbol belongs to."""
    auth_client.post(
        "/api/watchlist", json={"symbol": "BTCUSDT", "data_source": DataSource.BINANCE.value}
    )
    row = auth_client.get("/api/watchlist").json()[0]
    assert row["data_source"] == "binance"


def test_the_data_source_defaults_to_the_stock_feed(auth_client):
    auth_client.post("/api/watchlist", json={"symbol": "AAPL"})
    assert auth_client.get("/api/watchlist").json()[0]["data_source"] == "yfinance"


def test_one_persons_list_is_not_anothers(auth_client, db_session):
    other = _other_user(db_session)
    from app.models.watchlist import WatchlistItem

    db_session.add(WatchlistItem(user_id=other.id, symbol="SECRET"))
    db_session.commit()

    assert auth_client.get("/api/watchlist").json() == []
    assert auth_client.delete("/api/watchlist/SECRET").status_code == 404


def test_the_list_keeps_the_order_things_were_added(auth_client):
    """Not alphabetical: the owner puts the one they care about first, and
    re-sorting it every render would take that away."""
    for symbol in ("2330.TW", "AAPL", "0050.TW"):
        auth_client.post("/api/watchlist", json={"symbol": symbol})

    assert [row["symbol"] for row in auth_client.get("/api/watchlist").json()] == [
        "2330.TW",
        "AAPL",
        "0050.TW",
    ]


def test_the_watchlist_needs_a_login(client):
    assert client.get("/api/watchlist").status_code == 401


def test_a_blank_symbol_is_refused(auth_client):
    assert auth_client.post("/api/watchlist", json={"symbol": "   "}).status_code == 422
