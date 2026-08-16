import matplotlib.pyplot as plt
import datetime

class Dashboard:
    """核心儀表板，整合行情、策略訊號、帳號、通知狀態 (模擬版)"""

    def __init__(self):
        self.data = []
        self.signals = []
        self.accounts = []
        self.notifications = []

    def add_price_data(self, prices: list):
        self.data = prices

    def add_signal(self, signal: str):
        self.signals.append((datetime.datetime.now(), signal))

    def add_account(self, account_id: str):
        self.accounts.append(account_id)

    def add_notification(self, message: str):
        self.notifications.append((datetime.datetime.now(), message))

    def show(self):
        fig, ax = plt.subplots(figsize=(8, 5))
        if self.data:
            ax.plot(self.data, label="價格走勢")
            # 簡單均線
            if len(self.data) >= 5:
                ma = [sum(self.data[i-5:i])/5 for i in range(5, len(self.data)+1)]
                ax.plot(range(4, len(self.data)), ma, label="5日均線")
        ax.set_title("核心儀表板")
        ax.set_xlabel("時間")
        ax.set_ylabel("價格")
        ax.legend()

        # 顯示策略訊號、帳號、通知狀態
        print("=== 策略訊號 ===")
        for ts, sig in self.signals:
            print(f"{ts}: {sig}")

        print("\n=== 帳號列表 ===")
        for acc in self.accounts:
            print(f"- {acc}")

        print("\n=== 通知狀態 ===")
        for ts, msg in self.notifications:
            print(f"{ts}: {msg}")

        plt.show()


class GUI(Dashboard):
    """相容於 run_gui.py 的介面實作"""
    def show_chart(self, strategy_type: str, symbol: str, prices: list):
        self.add_price_data(prices)
        self.add_signal(f"均線分析: {strategy_type} 標的: {symbol}")
        self.show()
