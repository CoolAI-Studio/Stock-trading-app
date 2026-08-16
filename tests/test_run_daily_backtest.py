import pytest
from src.database_manager import DatabaseManager
import run_daily_backtest

def test_run_daily_backtest_creates_results(tmp_path):
    db_file = tmp_path / "test_trading_app.db"
    run_daily_backtest.run_daily_backtest(str(db_file))
    db = DatabaseManager(str(db_file))
    results = db.get_backtest_results("Daily_MA_Cross")
    assert len(results) >= 1
