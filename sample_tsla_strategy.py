class Strategy:
    def __init__(self):
        self.name = "TSLA_MA5_Trend"
        self.symbol = "TSLA"       # 綁定監控標的：TSLA
        self.prices = []

    def on_tick(self, current_price: float) -> str:
        self.prices.append(current_price)
        if len(self.prices) > 20:
            self.prices.pop(0)

        if len(self.prices) < 5:
            return "HOLD"

        ma5 = sum(self.prices[-5:]) / 5
        prev_price = self.prices[-2]

        # 黃金交叉買入，死亡交叉賣出
        if prev_price < ma5 and current_price > ma5:
            return "BUY"
        elif prev_price > ma5 and current_price < ma5:
            return "SELL"

        return "HOLD"