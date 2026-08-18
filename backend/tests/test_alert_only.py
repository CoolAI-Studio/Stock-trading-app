"""Alert-only strategies: a strategy whose BUY/SELL notifies the owner and
records the signal, but never creates a pending order."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from alembic.config import Config
from sqlalchemy import create_engine, event, text

from alembic import command
from app.models.enums import ChannelType, DataSource, NotificationStatus, OrderSide
from app.models.mixins import utcnow
from app.models.notification import NotificationChannel
from app.models.order import Order
from app.models.risk import RiskSettings
from app.models.strategy import Strategy, StrategyAlert
from app.models.user import User
from app.services import alerts, market_loop
from app.services.events import Event
from app.services.market_data.base import Quote
from app.services.market_data.providers.mock_provider import MockProvider
from app.services.market_data.service import MarketDataService
from app.services.notification import dispatcher
from app.services.notification.base import SendResult

ALWAYS_BUY_SOURCE = """
class Strategy:
    def __init__(self):
        self.name = "always_buy"
        self.symbol = "AAPL"

    def on_tick(self, current_price: float) -> str:
        return "BUY"
"""


def _make_user(db_session, email="alert@example.com") -> User:
    user = User(email=email, hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _make_strategy(db_session, user, alert_only=True, **overrides) -> Strategy:
    overrides.setdefault("is_active", True)
    overrides.setdefault("name", "test-strategy")
    overrides.setdefault("source_code", ALWAYS_BUY_SOURCE)
    strategy = Strategy(
        user_id=user.id,
        symbol="AAPL",
        code_hash="irrelevant-for-tests",
        alert_only=alert_only,
        **overrides,
    )
    db_session.add(strategy)
    db_session.commit()
    db_session.refresh(strategy)
    return strategy


def _make_channel(db_session, user) -> NotificationChannel:
    channel = NotificationChannel(
        user_id=user.id,
        channel_type=ChannelType.TELEGRAM,
        label="my-telegram",
        config_encrypted={"bot_token": "t", "chat_id": "123"},
        is_enabled=True,
    )
    db_session.add(channel)
    db_session.commit()
    return channel


def _set_alert_interval(db_session, user, seconds: int) -> None:
    row = db_session.query(RiskSettings).filter(RiskSettings.user_id == user.id).first()
    if row is None:
        row = RiskSettings(user_id=user.id)
        db_session.add(row)
    row.alert_interval_sec = seconds
    db_session.commit()


def _mock_service(symbol="AAPL", price=100.0) -> MarketDataService:
    return MarketDataService(providers={DataSource.YFINANCE: MockProvider({symbol: price})})


def _delivery_succeeds():
    response = MagicMock(status_code=200)
    response.json.return_value = {"ok": True}
    response.raise_for_status.return_value = None
    return patch("httpx.post", return_value=response)


def _delivery_fails():
    return patch("httpx.post", side_effect=httpx.ConnectError("telegram unreachable"))


def _tick(db_session):
    return market_loop.tick_once(db=db_session, market_data_service=_mock_service())


# ---- alert instead of order ----


def test_alert_only_strategy_creates_no_order(db_session):
    user = _make_user(db_session)
    _make_channel(db_session, user)
    _make_strategy(db_session, user, alert_only=True)

    with _delivery_succeeds():
        events = _tick(db_session)

    assert db_session.query(Order).count() == 0
    assert any(e.type == "strategy.alert" for e in events)


def test_normal_strategy_still_creates_a_pending_order(db_session, published_events):
    user = _make_user(db_session)
    _make_channel(db_session, user)
    _make_strategy(db_session, user, alert_only=False)

    with _delivery_succeeds():
        _tick(db_session)

    assert db_session.query(Order).count() == 1
    assert db_session.query(StrategyAlert).count() == 0
    # Observed on the bus, not on tick_once()'s return value: the order path
    # announces itself from inside create_pending_order.
    assert any(e.type == "order.created" for e in published_events)


def test_alert_row_records_who_what_and_when(db_session):
    user = _make_user(db_session)
    _make_channel(db_session, user)
    strategy = _make_strategy(db_session, user, alert_only=True)

    with _delivery_succeeds():
        _tick(db_session)

    alert = db_session.query(StrategyAlert).one()
    assert alert.user_id == user.id
    assert alert.strategy_id == strategy.id
    assert alert.symbol == "AAPL"
    assert alert.side == OrderSide.BUY
    assert Decimal(99) < alert.price < Decimal(101)
    assert alert.status == NotificationStatus.SENT
    assert alert.created_at is not None


def test_alert_only_strategy_records_its_last_signal(db_session):
    user = _make_user(db_session)
    _make_channel(db_session, user)
    strategy = _make_strategy(db_session, user, alert_only=True)

    with _delivery_succeeds():
        _tick(db_session)

    db_session.refresh(strategy)
    assert strategy.last_signal == "BUY"
    assert strategy.last_signal_at is not None


# ---- throttling ----


def test_interval_suppresses_a_second_alert_inside_the_window(db_session):
    user = _make_user(db_session)
    _make_channel(db_session, user)
    _make_strategy(db_session, user, alert_only=True)
    _set_alert_interval(db_session, user, 3600)

    with _delivery_succeeds():
        _tick(db_session)
        _tick(db_session)

    assert db_session.query(StrategyAlert).count() == 1


def test_interval_allows_an_alert_once_the_window_has_passed(db_session):
    user = _make_user(db_session)
    _make_channel(db_session, user)
    _make_strategy(db_session, user, alert_only=True)
    _set_alert_interval(db_session, user, 3600)

    with _delivery_succeeds():
        _tick(db_session)
        first = db_session.query(StrategyAlert).one()
        first.created_at = utcnow() - timedelta(seconds=3601)
        db_session.commit()
        _tick(db_session)

    assert db_session.query(StrategyAlert).count() == 2


def test_interval_zero_alerts_every_time(db_session):
    user = _make_user(db_session)
    _make_channel(db_session, user)
    _make_strategy(db_session, user, alert_only=True)
    _set_alert_interval(db_session, user, 0)

    with _delivery_succeeds():
        _tick(db_session)
        _tick(db_session)
        _tick(db_session)

    assert db_session.query(StrategyAlert).count() == 3


def test_the_clock_is_per_side(db_session):
    """A BUY inside the window must not silence a SELL for the same strategy."""
    user = _make_user(db_session)
    _make_channel(db_session, user)
    strategy = _make_strategy(db_session, user, alert_only=True)
    _set_alert_interval(db_session, user, 3600)

    with _delivery_succeeds():
        alerts.emit_alert(db_session, strategy, OrderSide.BUY, Decimal(100))
        alerts.emit_alert(db_session, strategy, OrderSide.SELL, Decimal(100))
        alerts.emit_alert(db_session, strategy, OrderSide.BUY, Decimal(100))

    assert db_session.query(StrategyAlert).count() == 2


# ---- delivery failure: retry, but bounded ----


def test_failed_delivery_does_not_start_the_clock(db_session):
    user = _make_user(db_session)
    _make_channel(db_session, user)
    _make_strategy(db_session, user, alert_only=True)
    _set_alert_interval(db_session, user, 3600)

    with _delivery_fails():
        _tick(db_session)

    failed = db_session.query(StrategyAlert).one()
    assert failed.status == NotificationStatus.FAILED

    # The owner never saw it, so the very next tick retries even though the
    # interval has not elapsed.
    with _delivery_succeeds():
        _tick(db_session)

    rows = db_session.query(StrategyAlert).order_by(StrategyAlert.id).all()
    assert len(rows) == 2
    assert rows[1].status == NotificationStatus.SENT

    # ...and now that one got through, the clock really has started.
    with _delivery_succeeds():
        _tick(db_session)
    assert db_session.query(StrategyAlert).count() == 2


def test_a_delivery_nobody_received_is_not_a_success(db_session):
    """No enabled channel means nothing was delivered -- recording that as a
    success would silence the strategy behind a clock that never ran."""
    user = _make_user(db_session)
    _make_strategy(db_session, user, alert_only=True)
    _set_alert_interval(db_session, user, 3600)

    with patch("httpx.post") as mock_post:
        _tick(db_session)

    mock_post.assert_not_called()
    assert db_session.query(StrategyAlert).one().status == NotificationStatus.FAILED


def test_notifications_disabled_silences_alert_only_strategies_too(db_session, monkeypatch):
    """NOTIFICATIONS_ENABLED is the owner's off switch for the whole
    notification subsystem. Alerts dispatch inline instead of through the
    bus, which is the only place main.py applies that switch, so turning
    notifications off has to be honoured here as well -- otherwise the one
    pipeline that exists purely to send notifications keeps sending them."""
    monkeypatch.setattr("app.config.settings.NOTIFICATIONS_ENABLED", False)
    user = _make_user(db_session)
    _make_channel(db_session, user)
    _make_strategy(db_session, user, alert_only=True)

    with patch("httpx.post") as mock_post:
        _tick(db_session)

    mock_post.assert_not_called()
    # Recorded, not swallowed: the strategy did fire, and an alert the owner
    # was never sent must not start the throttle clock.
    row = db_session.query(StrategyAlert).one()
    assert row.status == NotificationStatus.FAILED
    assert "disabled" in (row.error or "")


def test_retry_is_bounded_for_a_permanently_dead_channel(db_session):
    user = _make_user(db_session)
    _make_channel(db_session, user)
    _make_strategy(db_session, user, alert_only=True)
    _set_alert_interval(db_session, user, 3600)

    with _delivery_fails() as mock_post:
        for _ in range(8):
            _tick(db_session)

    assert mock_post.call_count == alerts.MAX_DELIVERY_ATTEMPTS
    assert db_session.query(StrategyAlert).count() == alerts.MAX_DELIVERY_ATTEMPTS


def test_bounded_retry_resumes_after_the_interval(db_session):
    """The bound is a fallback to the normal interval, not a permanent stop --
    a channel the owner has since fixed must start working again."""
    user = _make_user(db_session)
    _make_channel(db_session, user)
    _make_strategy(db_session, user, alert_only=True)
    _set_alert_interval(db_session, user, 3600)

    with _delivery_fails():
        for _ in range(8):
            _tick(db_session)

    for row in db_session.query(StrategyAlert).all():
        row.created_at = utcnow() - timedelta(seconds=3601)
    db_session.commit()

    with _delivery_succeeds():
        _tick(db_session)

    assert db_session.query(StrategyAlert).count() == alerts.MAX_DELIVERY_ATTEMPTS + 1


# ---- dispatcher ----


def test_dispatcher_reports_what_it_delivered(db_session):
    user = _make_user(db_session)
    _make_channel(db_session, user)

    with _delivery_succeeds():
        result = dispatcher.handle_event(
            Event(type="order.created", data={"order_id": 1, "user_id": user.id}), db=db_session
        )

    assert result.delivered == 1
    assert result.failed == 0
    assert result.ok is True


def test_dispatcher_reports_a_failed_channel(db_session):
    user = _make_user(db_session)
    _make_channel(db_session, user)

    with _delivery_fails():
        result = dispatcher.handle_event(
            Event(type="order.created", data={"order_id": 1, "user_id": user.id}), db=db_session
        )

    assert result.delivered == 0
    assert result.failed == 1
    assert result.ok is False
    assert result.error


def test_dispatcher_with_no_channels_is_not_a_success(db_session):
    user = _make_user(db_session)

    result = dispatcher.handle_event(
        Event(type="order.created", data={"order_id": 1, "user_id": user.id}), db=db_session
    )

    assert result.ok is False
    assert result.delivered == 0


def test_dispatcher_sends_nothing_when_notifications_are_disabled(db_session, monkeypatch):
    monkeypatch.setattr("app.config.settings.NOTIFICATIONS_ENABLED", False)
    user = _make_user(db_session)
    _make_channel(db_session, user)

    with patch("httpx.post") as mock_post:
        result = dispatcher.handle_event(
            Event(type="order.created", data={"order_id": 1, "user_id": user.id}), db=db_session
        )

    mock_post.assert_not_called()
    assert result.ok is False
    assert "disabled" in (result.error or "")


def test_alert_message_names_the_strategy_symbol_side_and_price():
    message = dispatcher._format_message(
        Event(
            type="strategy.alert",
            data={
                "strategy_id": 7,
                "strategy_name": "rsi-watch",
                "symbol": "AAPL",
                "side": "buy",
                "price": "123.45",
                "user_id": 1,
            },
        )
    )

    assert "rsi-watch" in message
    assert "AAPL" in message
    assert "123.45" in message
    assert "BUY" in message.upper()


def test_bus_does_not_send_an_alert_a_second_time(db_session):
    """market_loop delivers alerts inline (it needs the outcome) and still
    publishes the event; the bus-subscribed dispatcher must not re-send it."""
    user = _make_user(db_session)
    _make_channel(db_session, user)
    _make_strategy(db_session, user, alert_only=True)

    with _delivery_succeeds() as mock_post:
        events = _tick(db_session)
        alert_event = next(e for e in events if e.type == "strategy.alert")
        dispatcher.handle_event(alert_event, db=db_session)

    assert mock_post.call_count == 1


# ---- API ----


def test_strategy_create_and_update_expose_alert_only(auth_client):
    created = auth_client.post(
        "/api/strategies",
        json={"name": "watcher", "symbol": "AAPL", "source_code": ALWAYS_BUY_SOURCE},
    )
    assert created.status_code == 201
    assert created.json()["alert_only"] is False

    strategy_id = created.json()["id"]
    patched = auth_client.patch(f"/api/strategies/{strategy_id}", json={"alert_only": True})
    assert patched.status_code == 200
    assert patched.json()["alert_only"] is True


def test_strategy_can_be_created_alert_only(auth_client):
    created = auth_client.post(
        "/api/strategies",
        json={
            "name": "watcher",
            "symbol": "AAPL",
            "source_code": ALWAYS_BUY_SOURCE,
            "alert_only": True,
        },
    )
    assert created.status_code == 201
    assert created.json()["alert_only"] is True


def test_risk_settings_expose_alert_interval(auth_client):
    body = auth_client.get("/api/risk-settings").json()
    assert body["alert_interval_sec"] == 900
    # Distinct knob, not an alias for the order cooldown.
    assert "signal_cooldown_sec" in body

    updated = auth_client.put("/api/risk-settings", json={"alert_interval_sec": 60})
    assert updated.status_code == 200
    assert updated.json()["alert_interval_sec"] == 60
    assert updated.json()["signal_cooldown_sec"] == 300


def test_list_alerts_newest_first(auth_client, db_session):
    user = db_session.query(User).one()
    strategy = _make_strategy(db_session, user, alert_only=True)
    for price in (1, 2, 3):
        db_session.add(
            StrategyAlert(
                user_id=user.id,
                strategy_id=strategy.id,
                symbol="AAPL",
                side=OrderSide.BUY,
                price=Decimal(price),
                status=NotificationStatus.SENT,
            )
        )
    db_session.commit()

    body = auth_client.get("/api/alerts").json()
    assert [row["price"] for row in body] == ["3", "2", "1"]
    assert body[0]["strategy_id"] == strategy.id
    assert body[0]["side"] == "buy"


def test_list_alerts_honours_limit_and_strategy_filter(auth_client, db_session):
    user = db_session.query(User).one()
    watched = _make_strategy(db_session, user, alert_only=True, name="watched")
    other = _make_strategy(db_session, user, alert_only=True, name="other")
    for strategy in (watched, other):
        for _ in range(3):
            db_session.add(
                StrategyAlert(
                    user_id=user.id,
                    strategy_id=strategy.id,
                    symbol="AAPL",
                    side=OrderSide.BUY,
                    price=Decimal(1),
                    status=NotificationStatus.SENT,
                )
            )
    db_session.commit()

    assert len(auth_client.get("/api/alerts", params={"limit": 2}).json()) == 2
    filtered = auth_client.get("/api/alerts", params={"strategy_id": watched.id}).json()
    assert len(filtered) == 3
    assert {row["strategy_id"] for row in filtered} == {watched.id}


def test_alerts_are_scoped_to_their_owner(auth_client, db_session):
    stranger = _make_user(db_session, email="stranger@example.com")
    strategy = _make_strategy(db_session, stranger, alert_only=True)
    db_session.add(
        StrategyAlert(
            user_id=stranger.id,
            strategy_id=strategy.id,
            symbol="AAPL",
            side=OrderSide.BUY,
            price=Decimal(1),
            status=NotificationStatus.SENT,
        )
    )
    db_session.commit()

    assert auth_client.get("/api/alerts").json() == []


def test_list_alerts_requires_auth(client):
    assert client.get("/api/alerts").status_code == 401


# ---- migration ----


def test_migration_leaves_existing_rows_alone(tmp_path, monkeypatch):
    """The whole promise of the feature: turning it on must not change what
    strategies that already exist do. Verified against a populated DB, which
    is the case a NOT NULL column with no default would fail on."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'backfill.db'}", connect_args={"check_same_thread": False}
    )
    monkeypatch.setattr("app.db.session.engine", engine)
    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))

    command.upgrade(cfg, "1e5c5c7819b8")  # the revision just before alert_only
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, email, hashed_password, is_active, is_superuser, "
                "created_at, updated_at) VALUES (1, 'old@example.com', 'x', 1, 0, "
                "'2026-01-01', '2026-01-01')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO strategies (id, user_id, name, symbol, data_source, source_code, "
                "code_hash, is_active, default_quantity, warmup_bars, consecutive_errors, "
                "created_at, updated_at) VALUES (1, 1, 'legacy', 'AAPL', 'yfinance', 'x', 'h', "
                "1, 1, 30, 0, '2026-01-01', '2026-01-01')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO risk_settings (id, user_id, capital, stop_loss_pct, "
                "take_profit_pct, max_position_qty, max_order_notional, "
                "max_pending_orders_per_symbol, signal_cooldown_sec, created_at, updated_at) "
                "VALUES (1, 1, 0, 0.05, 0.10, 0, 0, 3, 300, '2026-01-01', '2026-01-01')"
            )
        )

    command.upgrade(cfg, "head")

    with engine.connect() as connection:
        assert connection.execute(text("SELECT alert_only FROM strategies")).scalar() == 0
        assert (
            connection.execute(text("SELECT alert_interval_sec FROM risk_settings")).scalar() == 900
        )
    engine.dispose()


# ---- end to end: a price series that oscillates across the threshold ----


class _FakeClock:
    """Drives both halves of the throttle -- the cutoff it compares against
    and the timestamp stamped on each recorded alert -- so hours of polling
    can be replayed in milliseconds."""

    def __init__(self) -> None:
        self.now = datetime(2026, 8, 18, 13, 30, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


class _StubSender:
    """Stands in for Telegram/email/web push: counts what actually went out."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, config: dict, message: str) -> SendResult:
        self.messages.append(message)
        return SendResult(ok=True)


class _FixedPriceProvider:
    """MockProvider walks the price randomly, which would cross the threshold
    on its own schedule. This one says exactly what the series says."""

    def __init__(self) -> None:
        self.price = 100.0

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        return {
            symbol: Quote(
                symbol=symbol,
                data_source=DataSource.YFINANCE,
                price=Decimal(str(self.price)),
                quote_time=datetime.now(UTC),
            )
            for symbol in symbols
        }


THRESHOLD_SOURCE = """
class Strategy:
    def __init__(self):
        self.name = "threshold_watch"
        self.symbol = "AAPL"

    def on_tick(self, current_price: float) -> str:
        return "BUY" if current_price > 100 else "SELL"
"""

_OSCILLATION_TICKS = 240
_POLL_SEC = 5


@pytest.fixture
def fake_clock(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(alerts, "utcnow", clock)

    def _stamp(_mapper, _connection, target):
        target.created_at = clock.now

    event.listen(StrategyAlert, "before_insert", _stamp)
    try:
        yield clock
    finally:
        event.remove(StrategyAlert, "before_insert", _stamp)


@pytest.fixture
def stub_sender(monkeypatch):
    sender = _StubSender()
    monkeypatch.setitem(dispatcher.SENDERS, ChannelType.TELEGRAM, sender)
    return sender


def _drive_oscillation(db_session, clock, interval_sec: int) -> int:
    """Polls a strategy whose threshold sits at 100 with a price that steps
    over and back under it on every tick -- the exact shape the interval
    exists for. Returns how many BUY/SELL signals the strategy produced."""
    user = _make_user(db_session)
    _make_channel(db_session, user)
    _make_strategy(db_session, user, alert_only=True, source_code=THRESHOLD_SOURCE)
    _set_alert_interval(db_session, user, interval_sec)

    provider = _FixedPriceProvider()
    service = MarketDataService(
        providers={DataSource.YFINANCE: provider}, ttl_sec={DataSource.YFINANCE: 0.0}
    )
    for tick in range(_OSCILLATION_TICKS):
        provider.price = 100.5 if tick % 2 == 0 else 99.5
        market_loop.tick_once(db=db_session, market_data_service=service)
        clock.advance(_POLL_SEC)
    return _OSCILLATION_TICKS


def test_oscillation_across_the_threshold_is_throttled_to_the_interval(
    db_session, fake_clock, stub_sender
):
    signals = _drive_oscillation(db_session, fake_clock, interval_sec=900)

    # 240 polls x 5s = 1200 simulated seconds, so a 900s interval opens two
    # windows, and BUY and SELL run their own clocks: 2 sides x 2 windows.
    assert signals == 240
    assert len(stub_sender.messages) == 4
    assert db_session.query(StrategyAlert).count() == 4
    assert db_session.query(Order).count() == 0


def test_interval_zero_notifies_on_every_one_of_those_signals(
    db_session, fake_clock, stub_sender
):
    signals = _drive_oscillation(db_session, fake_clock, interval_sec=0)

    assert signals == 240
    assert len(stub_sender.messages) == 240
    assert db_session.query(StrategyAlert).count() == 240
    assert db_session.query(Order).count() == 0
