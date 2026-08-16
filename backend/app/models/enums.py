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


class NotificationStatus(StrEnum):
    SENT = "sent"
    FAILED = "failed"
