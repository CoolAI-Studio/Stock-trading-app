"""K 棒的長期存放處，讓圖表活過一次重開機。

報價早就有自己的表——那就是為什麼抓不到 K 棒的時候價格還顯示得出來——而 K 棒一根
都沒存。Render 的免費方案閒置就休眠，所以每次醒來都得重新跟上游要一次，要不到就
是一張空圖。那個不對稱就是「圖表在線上很不可靠」的結構原因。

**存下來的不是快取。** 它只在抓不到的時候有東西可以畫，不是抓得到時的替代品——
market_data/service.py 把它讀進去的時候標成「不新鮮」，正是為了這件事。
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.enums import DataSource
from app.models.market import MarketBar
from app.services.market_data.base import Bar, Timeframe


def _utc(moment: datetime) -> datetime:
    """一律帶 UTC 時區。

    SQLite 不保留時區，所以存進去是 aware 的、讀出來是 naive 的。不在邊界補回
    來，兩件事會同時安靜地壞掉：跟新抓的那幾根比對日期永遠對不上，於是每一次抓
    取都被當成全新的資料再存一份；而分割的比對也永遠找不到重疊，於是永遠偵測不
    到分割。兩個都不會報錯。
    """
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _to_bar(row: MarketBar, symbol: str, timeframe: Timeframe) -> Bar:
    return Bar(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=_utc(row.ts),
        open=float(row.open),
        high=float(row.high),
        low=float(row.low),
        close=float(row.close),
        volume=float(row.volume) if row.volume is not None else None,
    )


def load(
    db: Session,
    data_source: DataSource,
    symbol: str,
    timeframe: Timeframe,
    limit: int,
) -> list[Bar]:
    """存著的最後 `limit` 根，最舊的在前。沒有就是空的，不是錯誤。"""
    rows = (
        db.execute(
            select(MarketBar)
            .where(
                MarketBar.data_source == data_source,
                MarketBar.symbol == symbol,
                MarketBar.timeframe == timeframe.value,
            )
            # 由新往舊取再反轉：limit 要的是「最近的那幾根」，而由舊往新取會在
            # 一支有二十年歷史的股票上把整段撈進記憶體再丟掉大半。
            .order_by(MarketBar.ts.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return [_to_bar(row, symbol, timeframe) for row in reversed(rows)]


def _split_happened(existing: dict[datetime, Decimal], fresh: list[Bar]) -> bool:
    """上游把已經存過的那幾根改寫了嗎。

    provider 回的是還原價（auto_adjust），所以一次分割會讓整段歷史換一組數字。
    只增不改的存法會在分割那天接出一根不存在的長黑——圖上看得到，而策略會照著它
    算，兩邊都不會有東西說那一根是假的。

    只看**重疊的日期**：新來的那幾根本來就不在庫裡，拿它們比較不出任何東西。
    """
    for bar in fresh:
        was = existing.get(_utc(bar.timestamp))
        if was is None:
            continue
        # 四位小數：還原價本來就會有除不盡的尾數，比到最後一位會把每一次抓取都
        # 判成分割，於是每次都整段重寫。
        if round(Decimal(str(bar.close)), 4) != round(was, 4):
            return True
    return False


def save(
    db: Session,
    data_source: DataSource,
    symbol: str,
    timeframe: Timeframe,
    bars: list[Bar],
) -> None:
    """把抓到的存起來。空的就什麼都不做——「這次沒抓到」不可以洗掉已經存著的。"""
    if not bars:
        return

    where = (
        MarketBar.data_source == data_source,
        MarketBar.symbol == symbol,
        MarketBar.timeframe == timeframe.value,
    )
    existing = {
        _utc(ts): close
        for ts, close in db.execute(select(MarketBar.ts, MarketBar.close).where(*where)).all()
    }

    if _split_happened(existing, bars):
        # 整段換掉，不是只換重疊的那幾根：分割改寫的是**所有**歷史，而留著沒被
        # 這次請求涵蓋到的舊資料，就是留著一段用舊價格算的圖。
        db.execute(delete(MarketBar).where(*where))
        db.flush()
        existing = {}

    for bar in bars:
        if _utc(bar.timestamp) in existing:
            continue
        db.add(
            MarketBar(
                data_source=data_source,
                symbol=symbol,
                timeframe=timeframe.value,
                ts=bar.timestamp,
                open=Decimal(str(bar.open)),
                high=Decimal(str(bar.high)),
                low=Decimal(str(bar.low)),
                close=Decimal(str(bar.close)),
                volume=Decimal(str(bar.volume)) if bar.volume is not None else None,
            )
        )
    db.flush()
