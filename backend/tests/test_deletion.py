"""Being able to throw things away.

Four of the app's list pages had no delete at all: backtest runs, alert
history, order history, and the notification send log. Test data, mistyped
runs and a year of delivery records all just accumulated, and the backtest
list silently evicts its oldest row at thirty -- so the one run worth keeping
as a baseline disappeared while thirty parameter experiments stayed.

Orders are the exception and deliberately so. A CONFIRMED order moved a
position and is what the per-strategy capital gate counts
(services/signals.py::_strategy_committed_cost), so deleting one silently
frees capital the owner is still holding and leaves the position disagreeing
with the order history. A PENDING order is a signal still awaiting an answer;
the answer is 拒絕, which keeps the record. Only decided-and-inert rows go.
"""

from datetime import timedelta
from decimal import Decimal

from app.enums import (
    ChannelType,
    NotificationStatus,
    OrderSide,
    OrderSource,
    OrderStatus,
)
from app.models.mixins import utcnow
from app.models.notification import NotificationChannel, NotificationLog
from app.models.order import Order
from app.models.strategy import Strategy, StrategyAlert
from app.models.user import User


def _user(db_session) -> User:
    return db_session.query(User).first()


def _strategy(db_session, user) -> Strategy:
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
    return strategy


def _alert(db_session, user, strategy, **kw) -> StrategyAlert:
    alert = StrategyAlert(
        user_id=user.id,
        strategy_id=strategy.id,
        symbol="AAPL",
        side=OrderSide.BUY,
        price=Decimal(100),
        status=NotificationStatus.SENT,
        **kw,
    )
    db_session.add(alert)
    db_session.commit()
    db_session.refresh(alert)
    return alert


def _order(db_session, user, status: OrderStatus) -> Order:
    order = Order(
        user_id=user.id,
        source=OrderSource.MANUAL,
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal(1),
        status=status,
    )
    db_session.add(order)
    db_session.commit()
    db_session.refresh(order)
    return order


def _log(db_session, user, channel) -> NotificationLog:
    log = NotificationLog(
        user_id=user.id,
        channel_id=channel.id,
        event="order.created",
        status=NotificationStatus.SENT,
        message="hi",
    )
    db_session.add(log)
    db_session.commit()
    db_session.refresh(log)
    return log


def _channel(db_session, user) -> NotificationChannel:
    channel = NotificationChannel(
        user_id=user.id,
        channel_type=ChannelType.TELEGRAM,
        label="phone",
        config_encrypted={"bot_token": "t", "chat_id": "1"},
    )
    db_session.add(channel)
    db_session.commit()
    db_session.refresh(channel)
    return channel


# --- backtest runs ---------------------------------------------------------


def test_a_backtest_run_can_be_deleted(auth_client):
    auth_client.post(
        "/api/strategies",
        json={"name": "s", "symbol": "AAPL", "source_code": "class Strategy:\n    pass"},
    )
    runs = auth_client.get("/api/backtests").json()
    # No run to delete yet is fine -- the point is the route exists and 404s
    # cleanly rather than not existing at all.
    assert runs == []
    assert auth_client.delete("/api/backtests/999").status_code == 404


def test_deleting_a_backtest_run_removes_it_from_the_list(auth_client, db_session):
    from app.models.backtest import BacktestRun

    user = _user(db_session)
    run = BacktestRun(
        user_id=user.id,
        strategy_id=None,
        strategy_name="s",
        symbol="AAPL",
        timeframe="1d",
        data_source="yfinance",
        source_code="class Strategy: pass",
        range_start=utcnow() - timedelta(days=30),
        range_end=utcnow(),
        code_hash="h",
        assumptions={},
        summary={},
        result={},
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)

    # Checked against the table rather than a read endpoint: every read
    # validates the full summary schema, which a run built by hand here
    # deliberately does not fill in, and building a real one would mean
    # running a whole backtest to test a delete.
    assert auth_client.delete(f"/api/backtests/{run.id}").status_code == 204
    assert db_session.get(BacktestRun, run.id) is None


# --- alert history ---------------------------------------------------------


def test_an_alert_can_be_deleted(auth_client, db_session):
    user = _user(db_session)
    alert = _alert(db_session, user, _strategy(db_session, user))

    assert auth_client.delete(f"/api/alerts/{alert.id}").status_code == 204
    assert auth_client.get("/api/alerts").json() == []


def test_the_whole_alert_history_can_be_cleared_at_once(auth_client, db_session):
    """Row by row is unusable for the thing this is for -- a watch-only
    strategy left running for a week produces hundreds."""
    user = _user(db_session)
    strategy = _strategy(db_session, user)
    for _ in range(5):
        _alert(db_session, user, strategy)

    resp = auth_client.delete("/api/alerts")
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 5
    assert auth_client.get("/api/alerts").json() == []


def test_clearing_alerts_can_be_narrowed_to_one_strategy(auth_client, db_session):
    user = _user(db_session)
    mine = _strategy(db_session, user)
    theirs = Strategy(
        user_id=user.id,
        name="other",
        symbol="TSLA",
        source_code="class Strategy: pass",
        code_hash="h",
        alert_only=True,
    )
    db_session.add(theirs)
    db_session.commit()
    db_session.refresh(theirs)
    _alert(db_session, user, mine)
    _alert(db_session, user, theirs)

    resp = auth_client.delete(f"/api/alerts?strategy_id={mine.id}")
    assert resp.json()["deleted"] == 1
    remaining = auth_client.get("/api/alerts").json()
    assert len(remaining) == 1
    assert remaining[0]["strategy_id"] == theirs.id


# --- order history ---------------------------------------------------------


def test_a_rejected_order_can_be_deleted(auth_client, db_session):
    order = _order(db_session, _user(db_session), OrderStatus.REJECTED)
    assert auth_client.delete(f"/api/orders/{order.id}").status_code == 204


def test_an_expired_order_can_be_deleted(auth_client, db_session):
    order = _order(db_session, _user(db_session), OrderStatus.EXPIRED)
    assert auth_client.delete(f"/api/orders/{order.id}").status_code == 204


def test_a_confirmed_order_is_refused_because_deleting_it_corrupts_the_books(
    auth_client, db_session
):
    """It moved a position, and the per-strategy capital gate counts it. Both
    would silently go wrong, and neither is visible from the orders page."""
    order = _order(db_session, _user(db_session), OrderStatus.CONFIRMED)

    resp = auth_client.delete(f"/api/orders/{order.id}")
    assert resp.status_code == 409
    assert "持倉" in resp.json()["detail"]
    assert auth_client.get(f"/api/orders/{order.id}").status_code == 200


def test_a_pending_order_is_refused_and_points_at_reject(auth_client, db_session):
    order = _order(db_session, _user(db_session), OrderStatus.PENDING)

    resp = auth_client.delete(f"/api/orders/{order.id}")
    assert resp.status_code == 409
    assert "拒絕" in resp.json()["detail"]


def test_clearing_order_history_leaves_the_ones_that_matter(auth_client, db_session):
    user = _user(db_session)
    _order(db_session, user, OrderStatus.REJECTED)
    _order(db_session, user, OrderStatus.EXPIRED)
    _order(db_session, user, OrderStatus.FAILED)
    confirmed = _order(db_session, user, OrderStatus.CONFIRMED)
    pending = _order(db_session, user, OrderStatus.PENDING)

    resp = auth_client.delete("/api/orders")
    assert resp.json()["deleted"] == 3

    left = {o["id"] for o in auth_client.get("/api/orders").json()}
    assert left == {confirmed.id, pending.id}


# --- notification send log -------------------------------------------------


def test_a_send_record_can_be_deleted(auth_client, db_session):
    user = _user(db_session)
    log = _log(db_session, user, _channel(db_session, user))

    assert auth_client.delete(f"/api/notifications/logs/{log.id}").status_code == 204
    assert auth_client.get("/api/notifications/logs").json() == []


def test_the_send_log_can_be_cleared(auth_client, db_session):
    """Nothing prunes this table. Every other log-like table in the app has a
    retention sweep; a few channels running for a year put tens of thousands
    of rows in here, on a free-tier database, with no way to remove them."""
    user = _user(db_session)
    channel = _channel(db_session, user)
    for _ in range(4):
        _log(db_session, user, channel)

    resp = auth_client.delete("/api/notifications/logs")
    assert resp.json()["deleted"] == 4
    assert auth_client.get("/api/notifications/logs").json() == []


def test_clearing_the_send_log_keeps_anything_still_queued_for_retry(auth_client, db_session):
    """A row with a retry due is not history -- it is a notification the owner
    has not received yet, and dropping it drops the delivery."""
    user = _user(db_session)
    channel = _channel(db_session, user)
    _log(db_session, user, channel)
    queued = NotificationLog(
        user_id=user.id,
        channel_id=channel.id,
        event="order.created",
        status=NotificationStatus.FAILED,
        message="pending delivery",
        attempts=1,
        next_retry_at=utcnow() + timedelta(seconds=30),
    )
    db_session.add(queued)
    db_session.commit()

    resp = auth_client.delete("/api/notifications/logs")
    assert resp.json()["deleted"] == 1
    left = auth_client.get("/api/notifications/logs").json()
    assert len(left) == 1
    assert left[0]["event"] == "order.created"


# --- everything is scoped to its owner -------------------------------------


def test_deleting_something_that_is_not_yours_is_a_404(auth_client, client, db_session):
    """Not a 403: a 403 confirms the row exists."""
    from app.core.security import hash_password

    other = User(email="someone-else@example.com", hashed_password=hash_password("pw12345678"))
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)

    strategy = _strategy(db_session, other)
    alert = _alert(db_session, other, strategy)

    assert auth_client.delete(f"/api/alerts/{alert.id}").status_code == 404
