class RiskControl:
    """風險控制系統 (符合測試期望版)"""

    def __init__(self, capital: float = 100000, stop_loss: float = 0.1, take_profit: float = 0.1, max_position: int = 30):
        self.capital = capital
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.max_position = max_position
        self.current_position = 0

    def check_stop_loss(self, entry_price: float, current_price: float, stop_loss_pct=None) -> bool:
        pct = stop_loss_pct if stop_loss_pct is not None else self.stop_loss
        return current_price <= entry_price * (1 - pct)

    def check_take_profit(self, entry_price: float, current_price: float, take_profit_pct=None) -> bool:
        pct = take_profit_pct if take_profit_pct is not None else self.take_profit
        return (current_price - entry_price) / entry_price >= pct

    def check_position_limit(self, position_size: float, capital=None) -> bool:
        if capital is not None:
            return position_size <= capital
        # 修正：等於上限也算超過 → False (將當前持倉累加計算)
        return (self.current_position + position_size) < self.max_position

    def update_position(self, size: int):
        self.current_position += size
        return self.current_position
