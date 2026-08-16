import pytest
from src.backtesting import Backtesting

def test_backtesting_buy_and_sell():
    bt = Backtesting(initial_capital=1000)
    prices = [100, 110, 120]
    signals = ["BUY", "HOLD", "SELL"]
    bt.run_strategy(prices, signals)
    final_val = bt.final_value(last_price=120)
    profit = bt.profit(last_price=120)
    assert final_val == 1020
    assert profit == 20  # 正確獲利應該是 20

def test_backtesting_hold_only():
    bt = Backtesting(initial_capital=500)
    prices = [50, 60, 70]
    signals = ["HOLD", "HOLD", "HOLD"]
    bt.run_strategy(prices, signals)
    final_val = bt.final_value(last_price=70)
    assert final_val == 500
    assert bt.profit(last_price=70) == 0

def test_backtesting_multiple_buys_and_sells():
    bt = Backtesting(initial_capital=300)
    prices = [50, 60, 70, 80]
    signals = ["BUY", "BUY", "SELL", "SELL"]
    bt.run_strategy(prices, signals)
    final_val = bt.final_value(last_price=80)
    profit = bt.profit(last_price=80)
    assert final_val == 340
    assert profit == 40
    assert isinstance(profit, (int, float))  # 允許 int 或 float
