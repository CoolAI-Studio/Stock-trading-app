class Strategy:
    """均線黃金交叉／死亡交叉。

    用系統內建的 indicators.sma，不要自己算——自己算出來的數字會跟回測、
    指標清單和一般看盤軟體對不起來，而畫面上不會有任何地方告訴你差在哪。

    on_bar 而不是 on_tick：on_tick 每分鐘會被餵好幾次即時報價，寫在裡面的
    「5 日均線」其實是「最近 5 筆報價的平均」，那支策略會被盤中雜訊觸發。
    """

    def __init__(self):
        self.name = "均線交叉"
        self.symbol = "2330.TW"
        self.fast = 5
        self.slow = 20
        self.closes = []

        # 慢線要 20 根才算得出來，多留幾根讓第一個訊號是穩的。
        self.warmup_bars = 30
        self.timeframe = "1d"

    def on_bar(self, bar) -> str:
        self.closes.append(bar.close)
        # 只留用得到的長度，記憶體不會隨時間一直長大。
        if len(self.closes) > self.slow * 5:
            self.closes.pop(0)

        fast_line = indicators.sma(self.closes, self.fast)  # noqa: F821
        slow_line = indicators.sma(self.closes, self.slow)  # noqa: F821

        # 指標回傳的清單前面會有 None（資料還不夠算的那幾根），
        # 而且要拿「這一根」和「上一根」比，才知道是不是剛剛交叉。
        if len(self.closes) < self.slow + 1:
            return "HOLD"
        fast_now, fast_prev = fast_line[-1], fast_line[-2]
        slow_now, slow_prev = slow_line[-1], slow_line[-2]
        if None in (fast_now, fast_prev, slow_now, slow_prev):
            return "HOLD"

        if fast_prev <= slow_prev and fast_now > slow_now:
            return "BUY"
        if fast_prev >= slow_prev and fast_now < slow_now:
            return "SELL"
        return "HOLD"
