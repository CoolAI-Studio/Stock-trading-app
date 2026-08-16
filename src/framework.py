from abc import ABC, abstractmethod

class BaseAPI(ABC):
    """抽象化 API 基底類別，所有券商/平台需繼承此類別"""

    @abstractmethod
    def connect(self):
        """建立連線"""
        pass

    @abstractmethod
    def get_price(self, symbol: str) -> float:
        """取得行情價格"""
        pass

    @abstractmethod
    def place_order(self, symbol: str, quantity: int, order_type: str) -> bool:
        """下單"""
        pass


class FutuAPI(BaseAPI):
    """富途 OpenD API 模擬版"""

    def connect(self):
        return True  # 模擬成功連線

    def get_price(self, symbol: str) -> float:
        # 模擬行情回傳
        return 100.0

    def place_order(self, symbol: str, quantity: int, order_type: str) -> bool:
        # 模擬下單成功
        return True


class TradingViewAPI(BaseAPI):
    """TradingView Webhook API 模擬版"""

    def connect(self):
        return True

    def get_price(self, symbol: str) -> float:
        return 200.0

    def place_order(self, symbol: str, quantity: int, order_type: str) -> bool:
        return True
