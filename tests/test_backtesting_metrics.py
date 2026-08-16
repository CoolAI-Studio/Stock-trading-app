import pytest
from src.backtesting import Backtesting

def test_sharpe_ratio_positive():
    bt = Backtesting([0.01, 0.02, -0.01, 0.03])
    assert bt.sharpe_ratio() > 0

def test_max_drawdown_negative():
    bt = Backtesting([0.01, -0.05, 0.02])
    assert bt.max_drawdown() <= 0

def test_run_strategy_and_results():
    bt = Backtesting([0.01, 0.02, -0.01])
    assert bt.run_strategy("MA_Cross") is True
    results = bt.get_results()
    assert results[0]["strategy"] == "MA_Cross"
