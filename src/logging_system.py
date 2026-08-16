import datetime

class LoggingSystem:
    """紀錄系統，支援策略執行、行情資料、下單紀錄 (模擬版)"""

    def __init__(self):
        self.logs = []

    def log_strategy(self, strategy_name: str, result: str):
        entry = (datetime.datetime.now(), "Strategy", strategy_name, result)
        self.logs.append(entry)
        return entry

    def log_market_data(self, symbol: str, price: float):
        entry = (datetime.datetime.now(), "MarketData", symbol, price)
        self.logs.append(entry)
        return entry

    def log_order(self, symbol: str, quantity: int, order_type: str):
        entry = (datetime.datetime.now(), "Order", symbol, f"{order_type} {quantity}")
        self.logs.append(entry)
        return entry

    def list_logs(self):
        return self.logs
