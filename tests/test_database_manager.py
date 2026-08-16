import pytest
import datetime
from src.database_manager import DatabaseManager

def test_insert_and_get_logs():
    db = DatabaseManager(":memory:")
    ts = datetime.datetime.now().isoformat()
    assert db.insert_log(ts, "order", "Buy HK.00700 100") is True
    logs = db.get_logs("order")
    assert logs[0][1] == "order"
    assert "Buy HK.00700" in logs[0][2]

def test_insert_and_get_backtest_results():
    db = DatabaseManager(":memory:")
    assert db.insert_backtest_result("MA_Cross", 120.5) is True
    results = db.get_backtest_results("MA_Cross")
    assert results[0][0] == "MA_Cross"
    assert results[0][1] == 120.5
