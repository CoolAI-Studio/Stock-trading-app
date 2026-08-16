import pytest
from src.risk_control import RiskControl

def test_position_limit():
    rc = RiskControl(capital=100000)
    assert rc.check_position_limit(100, 100) is True
    assert rc.check_position_limit(1000, 200) is False
