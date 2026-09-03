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

# 一段序列（來源＋代號＋週期）最多留幾根。
#
# 有上限，是因為這張表本來一根都不丟，而沒有任何端點、腳本或排程刪得掉它。
#
# 今天全 app 唯一的寫入者是圖表端點（`api/routers/market.py` 的 `GET /api/market/bars`，
# 全部呼叫端裡只有它傳 `db=`）：使用者每看一次圖就寫一次，而每一個（來源、代
# 號、週期）都是一段會一直往前滑的序列。盯盤迴圈現在**一列都不寫**（market_loop
# 呼叫 `get_bars()` 沒有傳 `db=`）——但 #59 要補的正是那個 `db=`，一補上去一支 1
# 分鐘的 on_bar 策略就是每個交易日約 390 根、一年十萬根。**所以這道上限必須先落
# 地，那條寫入路徑才能打開。**
#
# 更關鍵的是**沒有人讀得到超過這個深度的東西**——`load()` 在整個 app 裡只有一個
# 呼叫點（`market_data/service.py` 的 `_prime_from_storage`），而它固定問這麼深。所以第
# 一千根以外的每一列，都是只佔空間、不會被任何一條路讀到的資料。
#
# 而空間用完先失敗的是**寫入**：免費方案是整個資料庫 0.5GB，塞滿之後通知紀錄、重
# 送佇列、部位一起寫不進去。警告不能停擺是最高優先，一張「抓不到時用來墊底」的圖
# 表資料不值得為它把整個 app 停掉。使用者也不是工程師：他手上沒有 psql，所以清理
# 只能發生在他本來就會走到的路上（跟 strategy_versions 一樣，在寫的那一刻修剪）。
#
# 這道上限封的是**每一段序列的深度**，不是序列的數量：看過一次就不再更新的那些段依然
# 永遠躺在那裡（#61）。不要把這行讀成「這張表有上限了」。
#
# 同一個數字兩邊用：service.py 的 `MAX_STORED_BARS` 就是它。存得比讀得回來的深是
# 純粹的浪費，存得比讀的淺則是圖表少一段——一個數字，兩件事都不會發生。
MAX_STORED_BARS = 1_000


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


def _trim(db: Session, data_source: DataSource, symbol: str, timeframe: Timeframe) -> None:
    """超過上限就丟最舊的，而且一段序列各算各的。

    刪的條件是「比第 MAX_STORED_BARS 新的那一根還舊」，不是 `DELETE ... LIMIT`——
    後者 SQLite 和 Postgres 講的不是同一種話（`webhooks._prune_audit_log` 也是為了
    同一個理由改成拿鍵比大小）。這張表沒有 id，但 `ts` 在同一段序列裡就是單調的。

    三個條件缺一不可：漏掉任何一個，存台積電的分線就會順手把蘋果的日線刪掉，而那
    是一個不會報錯、只會讓另一支的圖突然變空的錯。
    """
    where = (
        MarketBar.data_source == data_source,
        MarketBar.symbol == symbol,
        MarketBar.timeframe == timeframe.value,
    )
    oldest_kept = db.execute(
        select(MarketBar.ts)
        .where(*where)
        .order_by(MarketBar.ts.desc())
        .offset(MAX_STORED_BARS - 1)
        .limit(1)
    ).scalar_one_or_none()
    if oldest_kept is None:
        # 還沒滿。空的也走這裡——沒有第 N 新的那一根，就沒有什麼可以丟。
        return
    db.execute(delete(MarketBar).where(*where, MarketBar.ts < oldest_kept))
    db.flush()


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

    # **先切再寫，不是寫完再刪。** 圖表往前拉的深度是倍增的（PriceChart 的
    # depth：300 → 600 → 1200 → 2400…），很容易就超過上限。全部寫進去再讓 `_trim`
    # 刪掉的話，**每一次**拉深的請求都會插 1400 列再刪 1400 列，淨值永遠是零（量過：
    # 深度 2400、冷快取，第二輪起 INSERT 1400 / DELETE 1400）——而那發生在使用者正在
    # 等的那個 HTTP 請求裡，在免費 Postgres 上還一路產 dead tuple。為了省空間而換來永
    # 久的寫入放大，方向是反的。切掉之後 INSERT 回到 0。
    #
    # 副作用只有一個：`_split_happened` 的比對窗口跟著縮到「抓回來的最新 1000 根」。
    # 實務上不影響——`existing` 本來就只剩最新 ≤1000 根，兩邊重疊完全一樣；唯一沒有
    # 重疊的情況是那段序列停擺超過 1000 根（日線約四年），而那種情況本來也偵測不
    # 到分割。
    bars = bars[-MAX_STORED_BARS:]

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
    # 在寫完的當下修剪，不另外排一支排程：排程要有人去按、去設、去看它有沒有在
    # 跑，而這個使用者不會做那三件事的任何一件。寫入這條路則是他每次看圖、每個盯
    # 盤週期都會走到的。
    _trim(db, data_source, symbol, timeframe)
