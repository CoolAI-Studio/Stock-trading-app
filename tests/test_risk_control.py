import pytest
from src.risk_control import RiskControl

def test_stop_loss_triggered():
    rc = RiskControl()
    assert rc.check_stop_loss(entry_price=100, current_price=90, stop_loss_pct=0.1) is True
    assert rc.check_stop_loss(entry_price=100, current_price=95, stop_loss_pct=0.1) is False

def test_take_profit_triggered():
    rc = RiskControl()
    assert rc.check_take_profit(entry_price=100, current_price=110, take_profit_pct=0.1) is True
    assert rc.check_take_profit(entry_price=100, current_price=105, take_profit_pct=0.1) is False

def test_position_limit():
    rc = RiskControl(max_position=50)
    rc.update_position(20)
    assert rc.check_position_limit(25) is True
    assert rc.check_position_limit(30) is False

def test_update_position():
    rc = RiskControl()
    rc.update_position(10)
    assert rc.current_position == 10
    rc.update_position(5)
    assert rc.current_position == 15
