class Strategy:
    """RSI 超賣買進、超買賣出。

    RSI 用 indicators.rsi，它跟看盤軟體一樣用 Wilder 平滑法。自己用簡單平均
    寫一版是最常見的錯誤——數字會很接近但不相等，於是你的策略、回測和
    TradingView 三邊各說各話。
    """

    def __init__(self):
        self.name = "RSI 超買超賣"
        self.symbol = "2330.TW"
        self.period = 14
        self.oversold = 30
        self.overbought = 70
        self.closes = []

        # Wilder 平滑要暖身，14 根只夠算出第一個值，給它兩倍比較穩。
        self.warmup_bars = 30
        self.timeframe = "1d"

    def on_bar(self, bar) -> str:
        self.closes.append(bar.close)
        if len(self.closes) > self.period * 6:
            self.closes.pop(0)

        if len(self.closes) <= self.period:
            return "HOLD"

        latest = indicators.rsi(self.closes, self.period)[-1]  # noqa: F821
        if latest is None:
            return "HOLD"

        if latest < self.oversold:
            return "BUY"
        if latest > self.overbought:
            return "SELL"
        return "HOLD"
