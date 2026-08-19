"""Getting your own data out of the app.

Every May there is a tax return, and at some point somebody's accountant wants
the year's fills. There was no way to produce that except reading the screen a
page at a time and typing it into a spreadsheet -- and the history page only
showed the most recent fifty rows anyway.

The file has to open cleanly in Excel, which for Traditional Chinese means a
UTF-8 BOM. Without it Excel reads the bytes as the system codepage and every
Chinese column header arrives as mojibake, which looks like the app produced
a broken file.
"""

from decimal import Decimal

from app.models.enums import NotificationStatus, OrderSide, OrderSource, OrderStatus
from app.models.mixins import utcnow
from app.models.order import Order
from app.models.strategy import Strategy, StrategyAlert
from app.models.user import User


def _user(db_session) -> User:
    return db_session.query(User).first()


def _order(db_session, user, **kw) -> Order:
    defaults = dict(
        source=OrderSource.MANUAL,
        symbol="2330.TW",
        side=OrderSide.BUY,
        quantity=Decimal(1000),
        status=OrderStatus.CONFIRMED,
        fill_price=Decimal(1000),
        filled_quantity=Decimal(1000),
        filled_at=utcnow(),
    )
    defaults.update(kw)
    order = Order(user_id=user.id, **defaults)
    db_session.add(order)
    db_session.commit()
    return order


def test_orders_come_out_as_a_csv(auth_client, db_session):
    _order(db_session, _user(db_session))

    resp = auth_client.get("/api/export/orders.csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert "2330.TW" in resp.text


def test_the_file_carries_a_bom_so_excel_reads_the_chinese(auth_client, db_session):
    """Without it Excel decodes the bytes as the system codepage and every
    Chinese header arrives as mojibake -- which reads as the app producing a
    broken file."""
    _order(db_session, _user(db_session))

    resp = auth_client.get("/api/export/orders.csv")
    assert resp.content.startswith(b"\xef\xbb\xbf")


def test_the_download_is_named_so_it_does_not_land_as_orders_csv_again(auth_client, db_session):
    _order(db_session, _user(db_session))

    disposition = auth_client.get("/api/export/orders.csv").headers["content-disposition"]
    assert "attachment" in disposition
    assert "orders" in disposition


def test_the_columns_are_readable_rather_than_column_names(auth_client, db_session):
    """The person opening this is doing their tax return, not reading a
    schema."""
    _order(db_session, _user(db_session))

    header = auth_client.get("/api/export/orders.csv").text.splitlines()[0]
    assert "代號" in header
    assert "成交價" in header


def test_the_export_reports_what_actually_filled(auth_client, db_session):
    """Not the requested quantity: a partial fill is what reached the position
    and what the tax figure is computed from."""
    _order(db_session, _user(db_session), quantity=Decimal(1000), filled_quantity=Decimal(300))

    rows = auth_client.get("/api/export/orders.csv").text
    assert "300" in rows


def test_only_your_own_orders_are_exported(auth_client, db_session):
    from app.core.security import hash_password

    other = User(email="nosy@example.com", hashed_password=hash_password("pw12345678"))
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)
    _order(db_session, other, symbol="SECRET")
    _order(db_session, _user(db_session))

    text = auth_client.get("/api/export/orders.csv").text
    assert "SECRET" not in text
    assert "2330.TW" in text


def test_an_empty_account_still_produces_a_file_with_headers(auth_client):
    """An empty download is confusing; a file with headers and no rows says
    plainly that there is nothing yet."""
    resp = auth_client.get("/api/export/orders.csv")
    assert resp.status_code == 200
    assert "代號" in resp.text


def test_positions_can_be_exported(auth_client):
    auth_client.patch("/api/positions/AAPL", json={"quantity": "10", "avg_entry_price": "200"})

    resp = auth_client.get("/api/export/positions.csv")
    assert resp.status_code == 200
    assert "AAPL" in resp.text


def test_alerts_can_be_exported(auth_client, db_session):
    user = _user(db_session)
    strategy = Strategy(
        user_id=user.id,
        name="watcher",
        symbol="AAPL",
        source_code="class Strategy: pass",
        code_hash="h",
        alert_only=True,
    )
    db_session.add(strategy)
    db_session.commit()
    db_session.refresh(strategy)
    db_session.add(
        StrategyAlert(
            user_id=user.id,
            strategy_id=strategy.id,
            symbol="AAPL",
            side=OrderSide.BUY,
            price=Decimal(100),
            status=NotificationStatus.SENT,
        )
    )
    db_session.commit()

    resp = auth_client.get("/api/export/alerts.csv")
    assert resp.status_code == 200
    assert "AAPL" in resp.text


def test_an_unknown_export_is_a_404_rather_than_an_empty_file(auth_client):
    assert auth_client.get("/api/export/nonsense.csv").status_code == 404


def test_exports_need_a_login(client):
    assert client.get("/api/export/orders.csv").status_code == 401
