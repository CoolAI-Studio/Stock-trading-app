import pytest
from src.monitor import Monitor

def test_monitor_seconds():
    monitor = Monitor()
    result = monitor.monitor_seconds(5, lambda: "秒級事件")
    assert result == "秒級事件"
    assert ("seconds", 5, "秒級事件") in monitor.events

def test_monitor_minutes():
    monitor = Monitor()
    result = monitor.monitor_minutes(1, lambda: "分級事件")
    assert result == "分級事件"
    assert ("minutes", 1, "分級事件") in monitor.events

def test_monitor_daily():
    monitor = Monitor()
    result = monitor.monitor_daily(lambda: "日級事件")
    assert result == "日級事件"
    assert ("daily", 1, "日級事件") in monitor.events
