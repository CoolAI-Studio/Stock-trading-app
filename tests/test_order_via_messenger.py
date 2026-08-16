import pytest
from src.order import OrderSystem

def test_line_order():
    system = OrderSystem()
    result = system.place_order_via_line("AAPL", 10, "BUY")
    assert result is True
    assert {"channel": "LINE", "symbol": "AAPL", "quantity": 10, "type": "BUY"} in system.orders

def test_telegram_order():
    system = OrderSystem()
    result = system.place_order_via_telegram("BTCUSD", 1, "SELL")
    assert result is True
    assert {"channel": "Telegram", "symbol": "BTCUSD", "quantity": 1, "type": "SELL"} in system.orders

def test_list_orders():
    system = OrderSystem()
    system.place_order_via_line("TSLA", 5, "BUY")
    system.place_order_via_telegram("ETHUSD", 2, "SELL")
    orders = system.list_orders()
    assert len(orders) == 2
    assert any(order["symbol"] == "TSLA" for order in orders)
    assert any(order["symbol"] == "ETHUSD" for order in orders)
