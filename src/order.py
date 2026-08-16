class OrderSystem:
    """下單系統，支援 LINE/Telegram 指令下單 (模擬版)"""

    def __init__(self):
        self.orders = []

    def place_order_via_line(self, symbol: str, quantity: int, order_type: str) -> bool:
        order = {"channel": "LINE", "symbol": symbol, "quantity": quantity, "type": order_type}
        self.orders.append(order)
        return True

    def place_order_via_telegram(self, symbol: str, quantity: int, order_type: str) -> bool:
        order = {"channel": "Telegram", "symbol": symbol, "quantity": quantity, "type": order_type}
        self.orders.append(order)
        return True

    def list_orders(self):
        return self.orders
