class Strategy:
    def __init__(self):
        self.name = "RSI_14_Threshold"
        self.symbol = "AAPL"
        self.period = 14
        self.oversold = 30
        self.overbought = 70
        self.prices = []

    def _rsi(self) -> float | None:
        if len(self.prices) <= self.period:
            return None

        window = self.prices[-(self.period + 1) :]
        gains = []
        losses = []
        # window and window[1:] are intentionally different lengths (the
        # classic pairwise-zip idiom) -- strict=True would raise every call.
        for prev, curr in zip(window, window[1:], strict=False):
            change = curr - prev
            if change >= 0:
                gains.append(change)
            else:
                losses.append(-change)

        avg_gain = sum(gains) / self.period
        avg_loss = sum(losses) / self.period
        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def on_tick(self, current_price: float) -> str:
        self.prices.append(current_price)
        if len(self.prices) > self.period * 3:
            self.prices.pop(0)

        rsi = self._rsi()
        if rsi is None:
            return "HOLD"

        if rsi < self.oversold:
            return "BUY"
        if rsi > self.overbought:
            return "SELL"
        return "HOLD"
