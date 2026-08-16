import pytest
from src.logging_system import LoggingSystem

def test_log_strategy():
    logger = LoggingSystem()
    entry = logger.log_strategy("DummyStrategy", "成功執行")
    assert entry[1] == "Strategy"
    assert entry[2] == "DummyStrategy"
    assert entry[3] == "成功執行"

def test_log_market_data():
    logger = LoggingSystem()
    entry = logger.log_market_data("AAPL", 150.5)
    assert entry[1] == "MarketData"
    assert entry[2] == "AAPL"
    assert entry[3] == 150.5

def test_log_order():
    logger = LoggingSystem()
    entry = logger.log_order("BTCUSD", 2, "BUY")
    assert entry[1] == "Order"
    assert entry[2] == "BTCUSD"
    assert "BUY 2" in entry[3]

def test_list_logs():
    logger = LoggingSystem()
    logger.log_strategy("S1", "OK")
    logger.log_market_data("TSLA", 700)
    logs = logger.list_logs()
    assert len(logs) == 2
    assert any(log[1] == "Strategy" for log in logs)
    assert any(log[1] == "MarketData" for log in logs)
