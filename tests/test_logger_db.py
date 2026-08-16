import pytest
from src.logger_db import LoggerDB

def test_log_and_retrieve():
    logger = LoggerDB(":memory:")
    assert logger.log_event("order", "Buy HK.00700 100") is True
    logs = logger.get_logs("order")
    assert logs[0][1] == "order"
