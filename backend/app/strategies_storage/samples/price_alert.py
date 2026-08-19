class Strategy:
    """最簡單的一種：價格碰到就提醒你。

    這支不判斷趨勢、不算指標，只做一件事——收盤價跌破或漲過你設的價位，
    就發一則通知。搭配策略設定裡的「只提醒」開關使用，它就不會產生訂單。

    要改成自己的：把下面三行的代號和兩個價位換掉就好。
    """

    def __init__(self):
        self.name = "台積電到價提醒"
        self.symbol = "2330.TW"
        self.buy_below = 950.0  # 跌破這個價位提醒我
        self.sell_above = 1200.0  # 漲過這個價位提醒我

        # 這支不需要歷史資料，來一根 K 棒就能判斷。
        self.warmup_bars = 1
        self.timeframe = "1d"

    def on_bar(self, bar) -> str:
        if bar.close <= self.buy_below:
            return "BUY"
        if bar.close >= self.sell_above:
            return "SELL"
        return "HOLD"
