"""Per-strategy risk overrides.

RiskSettings stays global and one-row-per-user. A Strategy may override any
of the eight knobs by setting a non-NULL value; NULL means inherit, which is
what every strategy that existed before this feature holds -- so turning it
on changes nothing until the owner opts a strategy in.
"""

from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command
from app.enums import (
    ChannelType,
    DataSource,
    NotificationStatus,
    OrderSide,
    OrderSource,
    OrderStatus,
)
from app.models.notification import NotificationChannel
from app.models.order import Order
from app.models.position import Position
from app.models.risk import RiskSettings
from app.models.strategy import Strategy, StrategyAlert
from app.models.user import User
from app.services import market_loop, portfolio, risk, risk_resolver
from app.services.market_data.providers.mock_provider import MockProvider
from app.services.market_data.service import MarketDataService
from app.services.signals import SignalIn, create_pending_order

ALWAYS_BUY_SOURCE = """
class Strategy:
    def __init__(self):
        self.name = "always_buy"
        self.symbol = "AAPL"

    def on_tick(self, current_price: float) -> str:
        return "BUY"
"""


def _make_user(db_session, email="overrides@example.com") -> User:
    user = User(email=email, hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _make_risk(db_session, user, **kwargs) -> RiskSettings:
    row = RiskSettings(user_id=user.id, **kwargs)
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def _make_strategy(db_session, user, name="test-strategy", **overrides) -> Strategy:
    overrides.setdefault("source_code", ALWAYS_BUY_SOURCE)
    strategy = Strategy(
        user_id=user.id,
        name=name,
        symbol="AAPL",
        code_hash="irrelevant-for-tests",
        **overrides,
    )
    db_session.add(strategy)
    db_session.commit()
    db_session.refresh(strategy)
    return strategy


def _make_position(db_session, user, **kwargs) -> Position:
    kwargs.setdefault("symbol", "AAPL")
    position = Position(user_id=user.id, **kwargs)
    db_session.add(position)
    db_session.commit()
    db_session.refresh(position)
    return position


def _signal(**overrides) -> SignalIn:
    base = dict(
        symbol="AAPL",
        side=OrderSide.BUY,
        source=OrderSource.STRATEGY,
        quantity=Decimal(1),
        signal_price=Decimal(100),
    )
    base.update(overrides)
    return SignalIn(**base)


def _reject(db_session, order) -> None:
    """Clears the pending-same-side dedupe so a later gate is what answers."""
    order.status = OrderStatus.REJECTED
    db_session.commit()


def _mock_service(symbol="AAPL", price=100.0) -> MarketDataService:
    return MarketDataService(providers={DataSource.YFINANCE: MockProvider({symbol: price})})


# A full set of non-default globals, so a resolved value can never accidentally
# match the global one just because both are the column default.
GLOBAL_VALUES = dict(
    capital=Decimal(50000),
    stop_loss_pct=Decimal("0.07"),
    take_profit_pct=Decimal("0.13"),
    max_position_qty=Decimal(500),
    max_order_notional=Decimal(9000),
    max_pending_orders_per_symbol=4,
    signal_cooldown_sec=600,
    alert_interval_sec=1200,
)

OVERRIDE_VALUES = dict(
    capital=Decimal(1000),
    stop_loss_pct=Decimal("0.02"),
    take_profit_pct=Decimal("0.03"),
    max_position_qty=Decimal(7),
    max_order_notional=Decimal(150),
    max_pending_orders_per_symbol=1,
    signal_cooldown_sec=30,
    alert_interval_sec=60,
)


# ---- the resolver ----


def test_every_overridable_field_is_inherited_when_null(db_session):
    user = _make_user(db_session)
    settings = _make_risk(db_session, user, **GLOBAL_VALUES)
    strategy = _make_strategy(db_session, user)

    effective = risk_resolver.resolve(settings, strategy)

    for field in risk_resolver.OVERRIDABLE_FIELDS:
        assert getattr(effective, field) == getattr(settings, field), field


def test_every_overridable_field_wins_when_set(db_session):
    user = _make_user(db_session)
    settings = _make_risk(db_session, user, **GLOBAL_VALUES)
    strategy = _make_strategy(db_session, user, **OVERRIDE_VALUES)

    effective = risk_resolver.resolve(settings, strategy)

    for field in risk_resolver.OVERRIDABLE_FIELDS:
        assert getattr(effective, field) == OVERRIDE_VALUES[field], field


def test_a_single_override_leaves_the_other_seven_inherited(db_session):
    user = _make_user(db_session)
    settings = _make_risk(db_session, user, **GLOBAL_VALUES)
    strategy = _make_strategy(db_session, user, stop_loss_pct=Decimal("0.02"))

    effective = risk_resolver.resolve(settings, strategy)

    assert effective.stop_loss_pct == Decimal("0.02")
    assert effective.take_profit_pct == GLOBAL_VALUES["take_profit_pct"]
    assert effective.capital == GLOBAL_VALUES["capital"]


def test_resolving_without_a_strategy_is_purely_global(db_session):
    user = _make_user(db_session)
    settings = _make_risk(db_session, user, **GLOBAL_VALUES)

    effective = risk_resolver.resolve(settings)

    assert effective.strategy_id is None
    assert effective.stop_loss_pct == GLOBAL_VALUES["stop_loss_pct"]


def test_resolve_reports_which_strategy_supplied_the_values(db_session):
    user = _make_user(db_session)
    settings = _make_risk(db_session, user)
    strategy = _make_strategy(db_session, user)

    assert risk_resolver.resolve(settings, strategy).strategy_id == strategy.id


def test_resolve_for_user_creates_the_global_row_when_missing(db_session):
    user = _make_user(db_session)

    effective = risk_resolver.resolve_for_user(db_session, user.id)

    assert effective.signal_cooldown_sec == 300  # the RiskSettings default
    assert db_session.query(RiskSettings).filter(RiskSettings.user_id == user.id).count() == 1


# ---- each field at its own gate ----


def test_signal_cooldown_override_applies_instead_of_the_global(db_session):
    user = _make_user(db_session)
    _make_risk(db_session, user, signal_cooldown_sec=0)
    strategy = _make_strategy(db_session, user, signal_cooldown_sec=3600)

    first = create_pending_order(db_session, user, _signal(strategy_id=strategy.id))
    _reject(db_session, first.order)
    second = create_pending_order(db_session, user, _signal(strategy_id=strategy.id))

    assert first.created is True
    assert second.created is False
    assert second.reason == "signal cooldown active"


def test_max_pending_orders_override_applies_instead_of_the_global(db_session):
    user = _make_user(db_session)
    _make_risk(db_session, user, max_pending_orders_per_symbol=4, signal_cooldown_sec=0)
    strategy = _make_strategy(db_session, user, max_pending_orders_per_symbol=1)

    buy = create_pending_order(db_session, user, _signal(strategy_id=strategy.id))
    sell = create_pending_order(
        db_session, user, _signal(side=OrderSide.SELL, strategy_id=strategy.id)
    )

    assert buy.created is True
    assert sell.created is False
    assert sell.reason == "max pending orders for this symbol reached"


def test_max_position_qty_override_applies_instead_of_the_global(db_session):
    user = _make_user(db_session)
    _make_risk(db_session, user, max_position_qty=Decimal(0), signal_cooldown_sec=0)
    strategy = _make_strategy(db_session, user, max_position_qty=Decimal(5))
    _make_position(db_session, user, quantity=Decimal(4), avg_entry_price=Decimal(10))

    result = create_pending_order(
        db_session, user, _signal(quantity=Decimal(2), strategy_id=strategy.id)
    )

    assert result.created is False
    assert result.reason == "position limit exceeded"


def test_max_order_notional_override_applies_instead_of_the_global(db_session):
    user = _make_user(db_session)
    _make_risk(db_session, user, max_order_notional=Decimal(0), signal_cooldown_sec=0)
    strategy = _make_strategy(db_session, user, max_order_notional=Decimal(100))

    result = create_pending_order(
        db_session, user, _signal(quantity=Decimal(2), strategy_id=strategy.id)
    )

    assert result.created is False
    assert result.reason == "order notional exceeds max_order_notional"


def test_position_uses_its_owning_strategys_stop_loss(db_session):
    user = _make_user(db_session)
    _make_risk(db_session, user, stop_loss_pct=Decimal("0.05"))
    strategy = _make_strategy(db_session, user, stop_loss_pct=Decimal("0.02"))
    _make_position(
        db_session,
        user,
        quantity=Decimal(10),
        avg_entry_price=Decimal(100),
        strategy_id=strategy.id,
    )

    # 97 is a 3% drawdown: inside the global 5%, past the strategy's own 2%.
    market_loop.tick_once(db=db_session, market_data_service=_mock_service(price=97.0))

    order = db_session.query(Order).filter(Order.side == OrderSide.SELL).one()
    assert order.risk_notes["trigger"] == "stop_loss"


def test_position_uses_its_owning_strategys_take_profit(db_session):
    user = _make_user(db_session)
    _make_risk(db_session, user, take_profit_pct=Decimal("0.10"))
    strategy = _make_strategy(db_session, user, take_profit_pct=Decimal("0.02"))
    _make_position(
        db_session,
        user,
        quantity=Decimal(10),
        avg_entry_price=Decimal(100),
        strategy_id=strategy.id,
    )

    market_loop.tick_once(db=db_session, market_data_service=_mock_service(price=103.0))

    order = db_session.query(Order).filter(Order.side == OrderSide.SELL).one()
    assert order.risk_notes["trigger"] == "take_profit"


def test_unattributed_position_keeps_using_the_global_stop_loss(db_session):
    user = _make_user(db_session)
    _make_risk(db_session, user, stop_loss_pct=Decimal("0.05"))
    _make_strategy(db_session, user, stop_loss_pct=Decimal("0.02"))
    _make_position(db_session, user, quantity=Decimal(10), avg_entry_price=Decimal(100))

    market_loop.tick_once(db=db_session, market_data_service=_mock_service(price=97.0))

    assert db_session.query(Order).filter(Order.side == OrderSide.SELL).count() == 0


def test_alert_interval_override_applies_instead_of_the_global(db_session):
    user = _make_user(db_session)
    db_session.add(
        NotificationChannel(
            user_id=user.id,
            channel_type=ChannelType.TELEGRAM,
            label="my-telegram",
            config_encrypted={"bot_token": "t", "chat_id": "123"},
            is_enabled=True,
        )
    )
    db_session.commit()
    # 0 globally means "notify on every signal" -- only the strategy's own
    # interval can suppress the second alert.
    _make_risk(db_session, user, alert_interval_sec=0)
    _make_strategy(db_session, user, alert_only=True, is_active=True, alert_interval_sec=3600)

    response = MagicMock(status_code=200)
    response.json.return_value = {"ok": True}
    response.raise_for_status.return_value = None
    with patch("httpx.post", return_value=response):
        market_loop.tick_once(db=db_session, market_data_service=_mock_service())
        market_loop.tick_once(db=db_session, market_data_service=_mock_service())

    assert db_session.query(StrategyAlert).count() == 1
    assert db_session.query(StrategyAlert).one().status == NotificationStatus.SENT


# ---- capital, as a pure gate ----


def test_capital_limit_allows_a_buy_that_fits():
    assert risk.check_capital_limit(Decimal(800), Decimal(150), Decimal(1000)) is True


def test_capital_limit_allows_spending_the_allocation_exactly():
    assert risk.check_capital_limit(Decimal(800), Decimal(200), Decimal(1000)) is True


def test_capital_limit_rejects_a_buy_that_breaches():
    assert risk.check_capital_limit(Decimal(800), Decimal(250), Decimal(1000)) is False


def test_capital_limit_unconfigured_allocation_means_no_limit():
    # Every row shipped with capital = 0 because it was never enforced;
    # reading that literally would block every order the day this lands.
    assert risk.check_capital_limit(Decimal(10**9), Decimal(10**9), Decimal(0)) is True


# ---- capital, at the order gate ----


def test_global_capital_blocks_a_breaching_buy(db_session):
    user = _make_user(db_session)
    _make_risk(db_session, user, capital=Decimal(1000))
    _make_position(db_session, user, quantity=Decimal(8), avg_entry_price=Decimal(100))

    result = create_pending_order(db_session, user, _signal(quantity=Decimal(5)))

    assert result.created is False
    assert "本金" in result.reason


def test_global_capital_allows_a_buy_that_fits(db_session):
    user = _make_user(db_session)
    _make_risk(db_session, user, capital=Decimal(1000))
    _make_position(db_session, user, quantity=Decimal(8), avg_entry_price=Decimal(100))

    result = create_pending_order(db_session, user, _signal(quantity=Decimal(2)))

    assert result.created is True


def test_strategy_capital_override_blocks_a_breaching_buy(db_session):
    user = _make_user(db_session)
    _make_risk(db_session, user, capital=Decimal(0), signal_cooldown_sec=0)
    strategy = _make_strategy(db_session, user, capital=Decimal(1000))

    opened = create_pending_order(
        db_session, user, _signal(quantity=Decimal(8), strategy_id=strategy.id)
    )
    _confirm(db_session, opened.order)

    result = create_pending_order(
        db_session, user, _signal(quantity=Decimal(5), strategy_id=strategy.id)
    )

    assert result.created is False
    assert "本金" in result.reason


def test_owning_a_position_you_did_not_pay_for_does_not_consume_your_allocation(db_session):
    """A position row belongs to whoever opened it, and it keeps belonging to
    them however much everyone else buys into it. Reading that row as "this
    strategy's money" charged the first opener for the whole thing -- here that
    is a 100 buy being billed 1000 and locked out of its own allocation."""
    user = _make_user(db_session)
    _make_risk(db_session, user, capital=Decimal(0), signal_cooldown_sec=0)
    mine = _make_strategy(db_session, user, name="mine", capital=Decimal(1000))
    theirs = _make_strategy(db_session, user, name="theirs")

    opened_by_mine = create_pending_order(
        db_session, user, _signal(quantity=Decimal(1), strategy_id=mine.id)
    )
    _confirm(db_session, opened_by_mine.order)
    piled_on_by_theirs = create_pending_order(
        db_session, user, _signal(quantity=Decimal(9), strategy_id=theirs.id)
    )
    _confirm(db_session, piled_on_by_theirs.order)

    position = portfolio.get_position(db_session, user.id, "AAPL")
    assert position.quantity == Decimal(10)
    assert position.strategy_id == mine.id, "opened by mine, so the row stays mine"

    result = create_pending_order(
        db_session, user, _signal(quantity=Decimal(5), strategy_id=mine.id)
    )

    assert result.created is True, "mine has 100 at work, not 1000; 500 more still fits"


def test_capital_zero_allows_everything(db_session):
    user = _make_user(db_session)
    _make_risk(db_session, user, capital=Decimal(0), signal_cooldown_sec=0)
    strategy = _make_strategy(db_session, user)
    _make_position(
        db_session,
        user,
        quantity=Decimal(10000),
        avg_entry_price=Decimal(1000),
        strategy_id=strategy.id,
    )

    result = create_pending_order(
        db_session, user, _signal(quantity=Decimal(10000), strategy_id=strategy.id)
    )

    assert result.created is True


def test_capital_gate_is_skipped_when_the_signal_carries_no_price(db_session):
    user = _make_user(db_session)
    _make_risk(db_session, user, capital=Decimal(1))
    _make_position(db_session, user, quantity=Decimal(8), avg_entry_price=Decimal(100))

    result = create_pending_order(db_session, user, _signal(signal_price=None))

    assert result.created is True


def test_capital_does_not_gate_a_sell(db_session):
    user = _make_user(db_session)
    _make_risk(db_session, user, capital=Decimal(1))
    _make_position(db_session, user, quantity=Decimal(8), avg_entry_price=Decimal(100))

    result = create_pending_order(db_session, user, _signal(side=OrderSide.SELL))

    assert result.created is True


# ---- position attribution ----


def _fill(db_session, user, strategy_id, side, price, quantity, symbol="AAPL") -> Position:
    order = Order(
        user_id=user.id,
        strategy_id=strategy_id,
        source=OrderSource.STRATEGY if strategy_id else OrderSource.MANUAL,
        symbol=symbol,
        side=side,
        quantity=quantity,
    )
    db_session.add(order)
    db_session.commit()
    return portfolio.apply_fill(db_session, order, price, quantity)


def test_opening_fill_attributes_the_position_to_its_strategy(db_session):
    user = _make_user(db_session)
    strategy = _make_strategy(db_session, user)

    position = _fill(db_session, user, strategy.id, OrderSide.BUY, Decimal(100), Decimal(10))

    assert position.strategy_id == strategy.id


def test_manual_fill_leaves_the_position_unattributed(db_session):
    user = _make_user(db_session)

    position = _fill(db_session, user, None, OrderSide.BUY, Decimal(100), Decimal(10))

    assert position.strategy_id is None


def test_adding_to_an_open_position_does_not_change_its_owner(db_session):
    user = _make_user(db_session)
    first = _make_strategy(db_session, user, name="first")
    second = _make_strategy(db_session, user, name="second")

    _fill(db_session, user, first.id, OrderSide.BUY, Decimal(100), Decimal(10))
    position = _fill(db_session, user, second.id, OrderSide.BUY, Decimal(120), Decimal(5))

    assert position.strategy_id == first.id


def test_going_flat_clears_the_attribution(db_session):
    user = _make_user(db_session)
    strategy = _make_strategy(db_session, user)

    _fill(db_session, user, strategy.id, OrderSide.BUY, Decimal(100), Decimal(10))
    position = _fill(db_session, user, strategy.id, OrderSide.SELL, Decimal(110), Decimal(10))

    assert position.quantity == 0
    assert position.strategy_id is None


def test_reopening_after_flat_attributes_to_the_new_strategy(db_session):
    user = _make_user(db_session)
    first = _make_strategy(db_session, user, name="first")
    second = _make_strategy(db_session, user, name="second")

    _fill(db_session, user, first.id, OrderSide.BUY, Decimal(100), Decimal(10))
    _fill(db_session, user, first.id, OrderSide.SELL, Decimal(110), Decimal(10))
    position = _fill(db_session, user, second.id, OrderSide.BUY, Decimal(120), Decimal(4))

    assert position.strategy_id == second.id


def test_partial_sell_keeps_the_attribution(db_session):
    user = _make_user(db_session)
    strategy = _make_strategy(db_session, user)

    _fill(db_session, user, strategy.id, OrderSide.BUY, Decimal(100), Decimal(10))
    position = _fill(db_session, user, strategy.id, OrderSide.SELL, Decimal(110), Decimal(4))

    assert position.strategy_id == strategy.id


# ---- API surface ----


def _current_user(db_session) -> User:
    return db_session.query(User).filter(User.email == "fixture-user@example.com").one()


def test_create_strategy_stores_the_overrides(auth_client):
    response = auth_client.post(
        "/api/strategies",
        json={
            "name": "with-overrides",
            "symbol": "AAPL",
            "source_code": ALWAYS_BUY_SOURCE,
            "capital": "2500",
            "stop_loss_pct": "0.02",
            "signal_cooldown_sec": 30,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["capital"] == "2500"
    assert body["stop_loss_pct"] == "0.02"
    assert body["signal_cooldown_sec"] == 30
    assert body["take_profit_pct"] is None


def test_create_strategy_without_overrides_leaves_them_inheriting(auth_client):
    response = auth_client.post(
        "/api/strategies",
        json={"name": "plain", "symbol": "AAPL", "source_code": ALWAYS_BUY_SOURCE},
    )

    body = response.json()
    for field in risk_resolver.OVERRIDABLE_FIELDS:
        assert body[field] is None, field


def test_update_strategy_can_set_and_clear_an_override(auth_client):
    created = auth_client.post(
        "/api/strategies",
        json={"name": "editable", "symbol": "AAPL", "source_code": ALWAYS_BUY_SOURCE},
    ).json()

    set_resp = auth_client.patch(
        f"/api/strategies/{created['id']}", json={"max_position_qty": "12"}
    )
    assert set_resp.json()["max_position_qty"] == "12"

    cleared = auth_client.patch(f"/api/strategies/{created['id']}", json={"max_position_qty": None})
    assert cleared.json()["max_position_qty"] is None


def test_position_read_names_the_strategy_it_is_attributed_to(auth_client, db_session):
    user = _current_user(db_session)
    strategy = _make_strategy(db_session, user)
    _make_position(
        db_session,
        user,
        quantity=Decimal(10),
        avg_entry_price=Decimal(100),
        strategy_id=strategy.id,
    )

    body = auth_client.get("/api/positions").json()

    assert body[0]["strategy_id"] == strategy.id


def test_flattening_a_position_clears_its_attribution(auth_client, db_session):
    user = _current_user(db_session)
    strategy = _make_strategy(db_session, user)
    position = _make_position(
        db_session,
        user,
        quantity=Decimal(10),
        avg_entry_price=Decimal(100),
        strategy_id=strategy.id,
    )

    assert auth_client.delete("/api/positions/AAPL").status_code == 204

    db_session.refresh(position)
    assert position.strategy_id is None


def test_deleting_a_strategy_releases_the_positions_it_owned(auth_client, db_session):
    """positions.strategy_id is declared ondelete="SET NULL", but SQLite only
    honours a foreign key when PRAGMA foreign_keys is on and nothing in this
    app turns it on -- so the declaration alone leaves the column pointing at
    a strategy that no longer exists.

    That is the one state where the exit scan and the positions page disagree:
    the scan resolves the missing id to None and quietly reverts to the global
    stop-loss, while the page still badges the position with the dead
    strategy. Cleared here for the same reason flatten_position clears it, and
    explicitly rather than by constraint so it holds on either backend.
    """
    user = _current_user(db_session)
    strategy = _make_strategy(db_session, user, stop_loss_pct=Decimal("0.02"))
    position = _make_position(
        db_session,
        user,
        quantity=Decimal(10),
        avg_entry_price=Decimal(100),
        strategy_id=strategy.id,
    )

    assert auth_client.delete(f"/api/strategies/{strategy.id}").status_code == 204

    db_session.refresh(position)
    assert position.strategy_id is None
    # And the owner is told so, rather than being shown a dead strategy's name.
    assert auth_client.get("/api/positions").json()[0]["strategy_id"] is None


def test_deleting_a_strategy_leaves_other_positions_attributed(auth_client, db_session):
    """The release is targeted: deleting one strategy must not knock every
    other position back onto the global thresholds."""
    user = _current_user(db_session)
    doomed = _make_strategy(db_session, user, name="doomed")
    survivor = _make_strategy(db_session, user, name="survivor")
    _make_position(
        db_session,
        user,
        quantity=Decimal(10),
        avg_entry_price=Decimal(100),
        strategy_id=doomed.id,
    )
    kept = _make_position(
        db_session,
        user,
        symbol="TSLA",
        quantity=Decimal(5),
        avg_entry_price=Decimal(200),
        strategy_id=survivor.id,
    )

    assert auth_client.delete(f"/api/strategies/{doomed.id}").status_code == 204

    db_session.refresh(kept)
    assert kept.strategy_id == survivor.id


# ---- migration against a populated database ----


def test_migration_leaves_existing_rows_inheriting(tmp_path, monkeypatch):
    """The safety promise: every strategy that already exists comes out NULL
    on all eight columns, i.e. still on the global values, and no position
    comes out attributed to a strategy that never opened it."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'overrides.db'}", connect_args={"check_same_thread": False}
    )
    monkeypatch.setattr("app.db.session.engine", engine)
    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))

    command.upgrade(cfg, "07b3c25465f6")  # the revision just before the overrides
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
                "code_hash, is_active, alert_only, default_quantity, warmup_bars, "
                "consecutive_errors, created_at, updated_at) VALUES (1, 1, 'legacy', 'AAPL', "
                "'yfinance', 'x', 'h', 1, 0, 1, 30, 0, '2026-01-01', '2026-01-01')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO positions (id, user_id, symbol, quantity, avg_entry_price, "
                "realized_pnl, created_at, updated_at) VALUES (1, 1, 'AAPL', 10, 100, 0, "
                "'2026-01-01', '2026-01-01')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO risk_settings (id, user_id, capital, stop_loss_pct, "
                "take_profit_pct, max_position_qty, max_order_notional, "
                "max_pending_orders_per_symbol, signal_cooldown_sec, alert_interval_sec, "
                "created_at, updated_at) "
                "VALUES (1, 1, 0, 0.05, 0.10, 0, 0, 3, 300, 900, '2026-01-01', '2026-01-01')"
            )
        )

    command.upgrade(cfg, "head")

    with engine.connect() as connection:
        columns = ", ".join(risk_resolver.OVERRIDABLE_FIELDS)
        row = connection.execute(text(f"SELECT {columns} FROM strategies")).one()
        assert list(row) == [None] * len(risk_resolver.OVERRIDABLE_FIELDS)
        assert connection.execute(text("SELECT strategy_id FROM positions")).scalar() is None
    engine.dispose()


def test_migrated_strategy_still_runs_on_the_global_settings(db_session):
    """The other half of the same promise, at the behaviour level: a strategy
    holding NULL everywhere is gated exactly as it was before the columns
    existed."""
    user = _make_user(db_session)
    _make_risk(db_session, user, signal_cooldown_sec=3600)
    strategy = _make_strategy(db_session, user)

    first = create_pending_order(db_session, user, _signal(strategy_id=strategy.id))
    _reject(db_session, first.order)
    second = create_pending_order(db_session, user, _signal(strategy_id=strategy.id))

    assert first.created is True
    assert second.reason == "signal cooldown active"


# ---- 0 means "off", and a 0 override is not an absent one ----
# Three states, and they must stay three: NULL = inherit the global, 0 = this
# knob is switched off for this strategy, a number = that limit. Collapsing 0
# into NULL would hand a strategy back the global stop-loss it just turned off.


def test_a_zero_override_is_kept_distinct_from_inheriting(db_session):
    user = _make_user(db_session)
    settings = _make_risk(db_session, user, **GLOBAL_VALUES)
    strategy = _make_strategy(
        db_session, user, **{field: 0 for field in risk_resolver.OVERRIDABLE_FIELDS}
    )

    effective = risk_resolver.resolve(settings, strategy)

    for field in risk_resolver.OVERRIDABLE_FIELDS:
        assert getattr(effective, field) == 0, field


def test_strategy_stop_loss_of_zero_switches_it_off_instead_of_inheriting(db_session):
    user = _make_user(db_session)
    _make_risk(db_session, user, stop_loss_pct=Decimal("0.05"), take_profit_pct=Decimal("0.10"))
    strategy = _make_strategy(db_session, user, stop_loss_pct=Decimal(0))
    _make_position(
        db_session,
        user,
        quantity=Decimal(10),
        avg_entry_price=Decimal(100),
        strategy_id=strategy.id,
    )

    # A 50% drawdown -- ten times the global stop. No exit, because this
    # strategy's stop-loss is off, not merely unset.
    market_loop.tick_once(db=db_session, market_data_service=_mock_service(price=50.0))

    assert db_session.query(Order).filter(Order.side == OrderSide.SELL).count() == 0


def test_strategy_take_profit_of_zero_switches_it_off_instead_of_inheriting(db_session):
    user = _make_user(db_session)
    _make_risk(db_session, user, stop_loss_pct=Decimal("0.05"), take_profit_pct=Decimal("0.10"))
    strategy = _make_strategy(db_session, user, take_profit_pct=Decimal(0))
    _make_position(
        db_session,
        user,
        quantity=Decimal(10),
        avg_entry_price=Decimal(100),
        strategy_id=strategy.id,
    )

    # A 50% gain -- five times the global target, and still no exit.
    market_loop.tick_once(db=db_session, market_data_service=_mock_service(price=150.0))

    assert db_session.query(Order).filter(Order.side == OrderSide.SELL).count() == 0


def test_strategy_max_pending_of_zero_switches_it_off_instead_of_inheriting(db_session):
    user = _make_user(db_session)
    _make_risk(db_session, user, max_pending_orders_per_symbol=1, signal_cooldown_sec=0)
    strategy = _make_strategy(db_session, user, max_pending_orders_per_symbol=0)

    buy = create_pending_order(db_session, user, _signal(strategy_id=strategy.id))
    # The global cap of 1 would refuse this second pending order for AAPL;
    # the strategy's 0 means it has no cap of its own to hit.
    sell = create_pending_order(
        db_session, user, _signal(side=OrderSide.SELL, strategy_id=strategy.id)
    )

    assert buy.created is True
    assert sell.created is True


def test_global_stop_loss_of_zero_switches_it_off(db_session):
    """The same rule one level up: an unattributed position resolves to the
    global row, and 0 there is off too."""
    user = _make_user(db_session)
    _make_risk(db_session, user, stop_loss_pct=Decimal(0), take_profit_pct=Decimal(0))
    _make_position(db_session, user, quantity=Decimal(10), avg_entry_price=Decimal(100))

    # Cost exactly: the old `current <= entry * (1 - 0)` fired right here.
    market_loop.tick_once(db=db_session, market_data_service=_mock_service(price=100.0))

    assert db_session.query(Order).filter(Order.side == OrderSide.SELL).count() == 0


# --- capital measures what a strategy SPENT, not what it happens to own -----
#
# The owner's rule, in their words: 本金上限就是上限，不可超過這個數值.
# Counting "positions this strategy opened" fails that in both directions --
# spending into someone else's position was free, and the first opener was
# billed for everyone else's buys.


def _confirm(db_session, order, fill_price=Decimal(100), fill_quantity=None) -> None:
    """A fill, the way the confirm endpoint records one -- including moving the
    position, since a test that marked the order confirmed but left the book
    untouched would be measuring a state the app can never actually be in."""
    quantity = order.quantity if fill_quantity is None else fill_quantity
    order.status = OrderStatus.CONFIRMED
    order.fill_price = fill_price
    order.filled_quantity = quantity
    db_session.commit()
    portfolio.apply_fill(db_session, order, fill_price, quantity)


def test_a_strategy_is_charged_for_buying_into_a_position_it_does_not_own(db_session):
    """Was the expensive hole: the gate looked at Position.strategy_id, so a
    strategy buying into a manual/webhook/other-strategy position contributed
    nothing to its own total and its ceiling never bound. Measured live at the
    time: a 1000 allocation spent past 2500 unblocked."""
    user = _make_user(db_session)
    _make_risk(db_session, user, capital=Decimal(0), signal_cooldown_sec=0)
    mine = _make_strategy(db_session, user, name="mine", capital=Decimal(1000))
    # Opened by hand, so it is nobody's -- exactly the case that used to be free.
    _make_position(db_session, user, quantity=Decimal(5), avg_entry_price=Decimal(100))

    first = create_pending_order(
        db_session, user, _signal(quantity=Decimal(8), strategy_id=mine.id)
    )
    assert first.created is True
    _confirm(db_session, first.order)

    second = create_pending_order(
        db_session, user, _signal(quantity=Decimal(5), strategy_id=mine.id)
    )
    assert second.created is False, "800 already spent of 1000; another 500 must not fit"
    assert "本金" in second.reason


def test_a_strategy_is_not_charged_for_another_strategys_spending(db_session):
    """The opposite direction, and just as wrong: the strategy that opened a
    position was billed for every other strategy's buys into it, so it hit a
    ceiling on money it never spent."""
    user = _make_user(db_session)
    _make_risk(db_session, user, capital=Decimal(0), signal_cooldown_sec=0)
    mine = _make_strategy(db_session, user, name="mine", capital=Decimal(1000))
    theirs = _make_strategy(db_session, user, name="theirs")

    spent_by_theirs = create_pending_order(
        db_session, user, _signal(quantity=Decimal(9), strategy_id=theirs.id)
    )
    _confirm(db_session, spent_by_theirs.order)

    result = create_pending_order(
        db_session, user, _signal(quantity=Decimal(9), strategy_id=mine.id)
    )
    assert result.created is True, "mine has spent nothing; theirs' 900 is not its bill"


def test_selling_gives_the_strategy_its_capital_back(db_session):
    """Otherwise a ceiling is a one-way ratchet: the strategy trades until it
    reaches the number once, then never trades again however much it closed."""
    user = _make_user(db_session)
    _make_risk(db_session, user, capital=Decimal(0), signal_cooldown_sec=0)
    strategy = _make_strategy(db_session, user, capital=Decimal(1000))

    bought = create_pending_order(
        db_session, user, _signal(quantity=Decimal(9), strategy_id=strategy.id)
    )
    _confirm(db_session, bought.order)

    blocked = create_pending_order(
        db_session, user, _signal(quantity=Decimal(5), strategy_id=strategy.id)
    )
    assert blocked.created is False

    sold = create_pending_order(
        db_session,
        user,
        _signal(side=OrderSide.SELL, quantity=Decimal(9), strategy_id=strategy.id),
    )
    _confirm(db_session, sold.order)

    again = create_pending_order(
        db_session, user, _signal(quantity=Decimal(9), strategy_id=strategy.id)
    )
    assert again.created is True, "the position was closed, so the allocation is free again"


def test_a_partial_fill_is_charged_at_what_actually_filled(db_session):
    """confirm_order accepts a fill smaller than the order and applies that
    smaller amount to the position, so charging the requested quantity would
    bill the strategy for shares it never received."""
    user = _make_user(db_session)
    _make_risk(db_session, user, capital=Decimal(0), signal_cooldown_sec=0)
    strategy = _make_strategy(db_session, user, capital=Decimal(1000))

    bought = create_pending_order(
        db_session, user, _signal(quantity=Decimal(9), strategy_id=strategy.id)
    )
    _confirm(db_session, bought.order, fill_quantity=Decimal(2))

    result = create_pending_order(
        db_session, user, _signal(quantity=Decimal(7), strategy_id=strategy.id)
    )
    assert result.created is True, "only 200 actually filled, so 700 more still fits in 1000"


def test_a_stop_loss_exit_is_attributed_to_the_strategy_that_owns_the_position(db_session):
    """The exit has to carry the owner, or the capital it frees is never
    credited back and the strategy is locked out by its own stop-loss."""
    user = _make_user(db_session)
    _make_risk(db_session, user, capital=Decimal(0), signal_cooldown_sec=0)
    strategy = _make_strategy(db_session, user, stop_loss_pct=Decimal("0.05"))
    _make_position(
        db_session,
        user,
        quantity=Decimal(10),
        avg_entry_price=Decimal(100),
        strategy_id=strategy.id,
    )

    market_loop.tick_once(db=db_session, market_data_service=_mock_service(price=80.0))

    exit_order = (
        db_session.query(Order)
        .filter(Order.side == OrderSide.SELL, Order.user_id == user.id)
        .first()
    )
    assert exit_order is not None, "a 20% drop against a 5% stop must produce an exit"
    assert exit_order.strategy_id == strategy.id


def test_the_global_ceiling_still_applies_inside_a_generous_strategy_allocation(db_session):
    """The two caps compose -- a strategy allocation is a limit on top of the
    book's, never a way around it. A strategy handed 100000 must still not be
    able to spend past the account's own 1000."""
    user = _make_user(db_session)
    _make_risk(db_session, user, capital=Decimal(1000), signal_cooldown_sec=0)
    strategy = _make_strategy(db_session, user, capital=Decimal(100000))

    opened = create_pending_order(
        db_session, user, _signal(quantity=Decimal(9), strategy_id=strategy.id)
    )
    _confirm(db_session, opened.order)

    result = create_pending_order(
        db_session, user, _signal(quantity=Decimal(5), strategy_id=strategy.id)
    )

    assert result.created is False
    assert "全域本金" in result.reason, "the global cap is what bit, and the message must say so"


# --- a signal the risk gate refused has to leave a trace --------------------
#
# create_pending_order returns a reason on all seven refusal paths, and two of
# its three callers surface it: the manual POST raises it as a 422, the
# TradingView webhook returns it in the response body. The worker loop read
# only `created` and dropped the reason on the floor -- so a strategy shouting
# BUY every tick and being refused every tick looked exactly like a strategy
# with nothing to say.


def test_a_signal_blocked_by_risk_is_recorded_on_the_strategy(db_session):
    user = _make_user(db_session)
    _make_risk(db_session, user, capital=Decimal(0), signal_cooldown_sec=0)
    strategy = _make_strategy(db_session, user, capital=Decimal(50), is_active=True)

    market_loop.tick_once(db=db_session, market_data_service=_mock_service(price=100.0))
    db_session.refresh(strategy)

    assert strategy.last_blocked_reason is not None, "the owner has to be able to see why"
    assert "本金" in strategy.last_blocked_reason
    assert strategy.last_blocked_at is not None
    # Untouched: nothing was signalled to anybody, same as before.
    assert strategy.last_signal is None


def test_a_blocked_signal_is_not_counted_as_a_strategy_error(db_session):
    """The strategy did its job. Routing this through last_error would put a
    working strategy on the error-backoff path and eventually disable it."""
    user = _make_user(db_session)
    _make_risk(db_session, user, capital=Decimal(0), signal_cooldown_sec=0)
    strategy = _make_strategy(db_session, user, capital=Decimal(50), is_active=True)

    market_loop.tick_once(db=db_session, market_data_service=_mock_service(price=100.0))
    db_session.refresh(strategy)

    assert strategy.last_error is None
    assert strategy.consecutive_errors == 0


def test_an_order_getting_through_clears_the_earlier_block(db_session):
    """Otherwise the reason is sticky and the owner keeps reading a stale
    explanation for a strategy that has since started trading fine."""
    user = _make_user(db_session)
    _make_risk(db_session, user, capital=Decimal(0), signal_cooldown_sec=0)
    strategy = _make_strategy(db_session, user, capital=Decimal(50), is_active=True)

    market_loop.tick_once(db=db_session, market_data_service=_mock_service(price=100.0))
    db_session.refresh(strategy)
    assert strategy.last_blocked_reason is not None

    strategy.capital = Decimal(100000)
    db_session.commit()
    market_loop.tick_once(db=db_session, market_data_service=_mock_service(price=100.0))
    db_session.refresh(strategy)

    assert strategy.last_signal == "BUY"
    assert strategy.last_blocked_reason is None
    assert strategy.last_blocked_at is None
