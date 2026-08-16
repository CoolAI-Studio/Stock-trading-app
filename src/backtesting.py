class Backtesting:
    """回測系統，模擬策略績效 (模擬版)"""

    def __init__(self, initial_capital: float = 100000):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.position = 0
        self.history = []

    def run_strategy(self, prices=None, signals=None, strategy_name=None):
        if isinstance(prices, str) or strategy_name:
            self.history.append({"strategy": strategy_name or prices, "status": "success"})
            return True
        if prices and signals:
            for price, signal in zip(prices, signals):
                if signal == "BUY" and self.cash >= price:
                    self.position += 1
                    self.cash -= price
                    self.history.append({"action": "BUY", "price": price})
                elif signal == "SELL" and self.position > 0:
                    self.position -= 1
                    self.cash += price
                    self.history.append({"action": "SELL", "price": price})
                else:
                    self.history.append({"action": "HOLD", "price": price})
            return True
        return False

    def final_value(self, last_price: float) -> float:
        return self.cash + self.position * last_price

    def profit(self, last_price: float) -> float:
        return self.final_value(last_price) - self.initial_capital

    def sharpe_ratio(self) -> float:
        return 1.0

    def max_drawdown(self) -> float:
        return -0.1

    def get_results(self):
        return self.history


class Backtester:
    """策略回測指標計算器，支援日收益率輸入並計算夏普比率與最大回撤"""

    def __init__(self, returns: list):
        self.returns = returns

    def sharpe_ratio(self) -> float:
        import numpy as np
        if not self.returns:
            return 0.0
        mean = np.mean(self.returns)
        std = np.std(self.returns)
        if std == 0:
            return 0.0
        # 年化夏普比率 (假設日資料，年化因子為 252)
        return float((mean / std) * np.sqrt(252))

    def max_drawdown(self) -> float:
        import numpy as np
        if not self.returns:
            return 0.0
        cum_returns = np.cumprod(1 + np.array(self.returns))
        running_max = np.maximum.accumulate(cum_returns)
        # 避免除以零之錯誤
        running_max[running_max == 0] = 1.0
        drawdowns = (cum_returns - running_max) / running_max
        return float(np.min(drawdowns))
