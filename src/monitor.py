import time

class Monitor:
    """監控系統，支援秒級、分級、日級監控 (模擬版)"""

    def __init__(self):
        self.events = []

    def monitor_seconds(self, interval: int, callback):
        """每 interval 秒執行一次 callback (模擬一次呼叫)"""
        time.sleep(0.01)  # 模擬延遲
        result = callback()
        self.events.append(("seconds", interval, result))
        return result

    def monitor_minutes(self, interval: int, callback):
        """每 interval 分鐘執行一次 callback (模擬一次呼叫)"""
        time.sleep(0.01)
        result = callback()
        self.events.append(("minutes", interval, result))
        return result

    def monitor_daily(self, callback):
        """每日執行一次 callback (模擬一次呼叫)"""
        time.sleep(0.01)
        result = callback()
        self.events.append(("daily", 1, result))
        return result
