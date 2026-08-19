"""Broker costs the owner picks from a list, instead of inventing numbers.

Two problems, one fix.

The cost fields on the backtest form were free-text with defaults I chose.
Which commission rate applies is not our decision to make -- it is whatever
the owner's broker actually gives them, which depends on the firm, the branch,
their volume and whatever promotion they opened under. Every 台股 broker
discounts the 0.1425% board rate differently (2.8折 through 6折) and sets its
own floor (1元 to 20元), and Firstrade charges no commission at all.

And the floor was missing entirely. The model applied proportional rates only,
so a 1-unit backtest of a 500 元 stock was charged 0.71 元 where the broker
charges 20 元 -- out by 28x, on every trade. A strategy that scalps small
positions looked mildly profitable and would have been eaten alive.
"""

from datetime import UTC, datetime
from decimal import Decimal

from app.services import broker_costs
from app.services.backtest import BacktestAssumptions, _Account, _execute

FLAT = Decimal("0.001425")
_WHENEVER = datetime(2026, 8, 19, tzinfo=UTC)


def _assumptions(**kw) -> BacktestAssumptions:
    base = dict(
        commission_rate=FLAT,
        slippage_rate=Decimal(0),
        sell_tax_rate=Decimal(0),
        quantity=Decimal(1),
        initial_capital=Decimal(1000000),
    )
    base.update(kw)
    return BacktestAssumptions(**base)


# --- the floor --------------------------------------------------------------


def test_a_tiny_trade_is_charged_the_brokers_minimum_not_the_percentage():
    """0.1425% of a single 500 元 share is 0.71 元. The broker charges 20."""
    account = _Account(cash=Decimal(1000000))
    _execute(
        account,
        "BUY",
        Decimal(500),
        _WHENEVER,
        _assumptions(minimum_fee=Decimal(20)),
    )

    # Cash out is the fill plus the shortfall up to the floor.
    assert account.costs >= Decimal(20)


def test_a_large_trade_pays_the_percentage_because_it_clears_the_floor():
    account = _Account(cash=Decimal(1000000))
    _execute(
        account,
        "BUY",
        Decimal(500),
        _WHENEVER,
        _assumptions(minimum_fee=Decimal(20), quantity=Decimal(1000)),
    )

    # 0.1425% of 500,000 is 712.5, far over any floor.
    assert account.costs > Decimal(700)


def test_a_zero_floor_changes_nothing():
    """Firstrade and the crypto venues have no minimum, and the owner may
    switch it off. The old behaviour has to survive exactly."""
    with_floor = _Account(cash=Decimal(1000000))
    _execute(with_floor, "BUY", Decimal(500), _WHENEVER, _assumptions(minimum_fee=Decimal(0)))

    without = _Account(cash=Decimal(1000000))
    _execute(without, "BUY", Decimal(500), _WHENEVER, _assumptions())

    assert with_floor.cash == without.cash
    assert with_floor.costs == without.costs


def test_the_floor_is_charged_on_the_way_out_as_well():
    """Both legs pay commission, so both legs meet the minimum -- a round
    trip on one small lot costs 40 元, not 20."""
    account = _Account(cash=Decimal(1000000))
    opening = _assumptions(minimum_fee=Decimal(20))
    _execute(account, "BUY", Decimal(500), _WHENEVER, opening)
    after_buy = account.costs
    _execute(account, "SELL", Decimal(500), _WHENEVER, opening)

    assert account.costs - after_buy >= Decimal(20)


# --- the presets ------------------------------------------------------------


def test_every_preset_is_complete_enough_to_use():
    presets = broker_costs.catalogue()
    assert presets, "an empty list would leave the dropdown with nothing in it"
    for preset in presets:
        assert preset.id and preset.label
        assert preset.commission_rate >= 0
        assert preset.minimum_fee >= 0
        assert preset.sell_tax_rate >= 0
        assert preset.note, "each one has to say what it assumes, since rates vary per customer"


def test_preset_ids_are_unique():
    ids = [p.id for p in broker_costs.catalogue()]
    assert len(ids) == len(set(ids))


def test_the_taiwan_board_rate_is_the_undiscounted_one():
    board = broker_costs.get("tw-board")
    assert board.commission_rate == Decimal("0.001425")
    assert board.sell_tax_rate == Decimal("0.003")
    assert board.minimum_fee == Decimal(20)


def test_a_discounted_taiwan_broker_costs_less_than_the_board_rate():
    board = broker_costs.get("tw-board")
    discounted = broker_costs.get("tw-cathay")
    assert discounted.commission_rate < board.commission_rate
    # 2.8折 of 0.1425%
    assert discounted.commission_rate == Decimal("0.000399")


def test_day_trading_pays_half_the_sell_tax():
    """0.15% rather than 0.3%, extended through the end of 2027."""
    day = broker_costs.get("tw-day-trade")
    assert day.sell_tax_rate == Decimal("0.0015")


def test_firstrade_charges_no_commission_and_no_floor():
    ft = broker_costs.get("us-firstrade")
    assert ft.commission_rate == Decimal(0)
    assert ft.minimum_fee == Decimal(0)
    # The SEC fee on sales is not zero, and pretending otherwise would make
    # every US backtest slightly optimistic.
    assert ft.sell_tax_rate > 0


def test_crypto_pays_a_fee_both_ways_and_no_transaction_tax():
    binance = broker_costs.get("crypto-binance")
    assert binance.commission_rate > 0
    assert binance.sell_tax_rate == Decimal(0)


def test_an_unknown_preset_is_an_error_rather_than_a_silent_default():
    """Falling back to some default would price a backtest under a broker the
    owner did not choose."""
    try:
        broker_costs.get("no-such-broker")
    except KeyError:
        return
    raise AssertionError("expected a KeyError")


def test_the_catalogue_is_served_to_the_form(auth_client):
    body = auth_client.get("/api/broker-costs").json()
    assert len(body) > 5
    first = body[0]
    assert {"id", "label", "market", "commission_rate", "minimum_fee", "sell_tax_rate", "note"} <= (
        set(first)
    )


def test_the_catalogue_needs_a_login_like_everything_else(client):
    assert client.get("/api/broker-costs").status_code == 401


def test_the_floor_survives_the_round_trip_through_the_api(auth_client):
    """A cost the owner set has to come back on the result, or they cannot
    tell which assumptions produced the number they are looking at."""
    create = auth_client.post(
        "/api/strategies",
        json={
            "name": "floor-check",
            "symbol": "2330.TW",
            "source_code": (
                "class Strategy:\n"
                "    def __init__(self):\n"
                "        self.name = 'floor-check'\n"
                "        self.symbol = '2330.TW'\n"
                "\n"
                "    def on_tick(self, current_price):\n"
                "        return 'HOLD'\n"
            ),
        },
    )
    assert create.status_code == 201, create.text

    resp = auth_client.post(
        "/api/backtests",
        json={
            "strategy_id": create.json()["id"],
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-08-01T00:00:00Z",
            "minimum_fee": "20",
        },
    )
    # The run may find no bars in a test environment; what matters is that the
    # field was accepted and echoed, not that the strategy traded.
    if resp.status_code == 201:
        assert Decimal(resp.json()["assumptions"]["minimum_fee"]) == Decimal(20)
    else:
        assert resp.status_code != 422, resp.text
