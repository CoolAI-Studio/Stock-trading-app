from datetime import date

from pydantic import BaseModel

from app.schemas.common import UtcDatetime


class DailySummaryMarket(BaseModel):
    market: str
    label: str
    symbols: list[str]
    # 那個市場當地的交易日（見 models/daily_summary.py）。
    done_on: date | None


class DailySummaryChannels(BaseModel):
    """啟用中的通知管道有幾個，其中幾個會收收盤摘要。

    兩個數字都要：「一個都沒有」和「有、但都沒勾」要他做的事不一樣。
    """

    enabled: int
    receiving: int


class DailySummaryRead(BaseModel):
    """畫面上那一格要說得出的三件事：會摘要哪幾檔、哪幾檔不會、上一次送出和最後的問題。"""

    is_enabled: bool
    channels: DailySummaryChannels
    markets: list[DailySummaryMarket]
    # 加密貨幣之類不收盤的：它們在自選股裡，但不會出現在任何一則摘要裡。
    unsupported: list[str]
    last_sent_at: UtcDatetime | None
    last_error: str | None


class DailySummaryUpdate(BaseModel):
    is_enabled: bool
