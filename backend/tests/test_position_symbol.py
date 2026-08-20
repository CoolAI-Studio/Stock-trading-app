"""A position invented from whatever string was in the URL.

PATCH /positions/{symbol} creates a Position row from the path parameter with
no validation of any kind -- `symbol.upper()` and straight into the database.
Every other symbol entrance was taught to refuse a company name and a bare
Taiwanese code today; this one was missed, and it is the worst place to miss,
because the market loop then polls that row and fires stop-loss and
take-profit alerts off it.

    PATCH /positions/台積電   -> a position in 「台積電」, which never prices,
                               so its stop-loss is never checked. Silent.
    PATCH /positions/2330    -> a position in 「2330」, which Yahoo prices as an
                               unrelated Japanese company. Not silent: it
                               fires, on the wrong company's price.
"""

from decimal import Decimal

from app.models.position import Position


def _payload(**kw) -> dict:
    body = {"quantity": "1000", "avg_entry_price": "950"}
    body.update(kw)
    return body


def test_a_company_name_cannot_become_a_position(auth_client, db_session):
    resp = auth_client.patch("/api/positions/台積電", json=_payload())

    assert resp.status_code == 422, resp.text
    assert db_session.query(Position).count() == 0


def test_the_refusal_names_the_symbol_to_use_instead(auth_client):
    resp = auth_client.patch("/api/positions/台積電", json=_payload())

    assert "2330" in resp.text


def test_a_bare_taiwanese_code_cannot_become_a_position(auth_client, db_session):
    """The dangerous one: it does not fail, it prices a Japanese company and
    then checks a stop-loss against that."""
    resp = auth_client.patch("/api/positions/2330", json=_payload())

    assert resp.status_code == 422
    assert db_session.query(Position).count() == 0


def test_a_qualified_symbol_still_works(auth_client, db_session):
    resp = auth_client.patch("/api/positions/2330.TW", json=_payload())

    assert resp.status_code == 200, resp.text
    assert db_session.query(Position).one().symbol == "2330.TW"


def test_a_us_ticker_still_works(auth_client, db_session):
    assert auth_client.patch("/api/positions/AAPL", json=_payload()).status_code == 200
    assert db_session.query(Position).one().symbol == "AAPL"


def test_a_lowercase_symbol_is_normalised(auth_client, db_session):
    auth_client.patch("/api/positions/2330.tw", json=_payload())

    assert db_session.query(Position).one().symbol == "2330.TW"


def test_an_existing_position_is_still_adjusted_rather_than_duplicated(auth_client, db_session):
    auth_client.patch("/api/positions/AAPL", json=_payload())
    auth_client.patch("/api/positions/AAPL", json=_payload(quantity="500"))

    position = db_session.query(Position).one()
    assert position.quantity == Decimal(500)
