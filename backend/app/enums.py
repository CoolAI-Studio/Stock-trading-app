"""跨層共用的列舉。**零相依**，而且必須維持零相依。

它原本住在 `app/models/enums.py`，而那個位置有一個看不見的代價：Python 一定會先
跑套件的 `__init__.py`，於是任何人只要 import 一個列舉，就把整個 SQLAlchemy 模型
層一起載進來。量到的數字：

    裸 Python                              187 ms
    app.models.enums（觸發套件 __init__）  2171 ms
    indicators（策略沙箱真正需要的）          285 ms

策略沙箱只是為了一個 `DataSource`，就付兩秒。而 #18 要把策略移到子行程執行之後，
那兩秒會變成「worker 重建期間策略是瞎的」——輪詢週期才五秒。

所以這個檔案搬到這裡，而且 tests/test_the_sandbox_does_not_drag_the_orm_along.py
會在有人替它加上相依時變紅。
"""

from enum import StrEnum


class DataSource(StrEnum):
    YFINANCE = "yfinance"
    BINANCE = "binance"


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderSource(StrEnum):
    STRATEGY = "strategy"
    TRADINGVIEW = "tradingview"
    MANUAL = "manual"


class OrderStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    FAILED = "failed"


class ChannelType(StrEnum):
    LINE = "line"
    TELEGRAM = "telegram"
    EMAIL = "email"
    WEB_PUSH = "web_push"


class NotificationStatus(StrEnum):
    SENT = "sent"
    FAILED = "failed"
