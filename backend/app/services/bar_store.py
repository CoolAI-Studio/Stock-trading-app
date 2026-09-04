"""K 棒的長期存放處，讓圖表活過一次重開機。

報價早就有自己的表——那就是為什麼抓不到 K 棒的時候價格還顯示得出來——而 K 棒一根
都沒存。Render 的免費方案閒置就休眠，所以每次醒來都得重新跟上游要一次，要不到就
是一張空圖。那個不對稱就是「圖表在線上很不可靠」的結構原因。

**存下來的不是快取。** 它只在抓不到的時候有東西可以畫，不是抓得到時的替代品——
market_data/service.py 把它讀進去的時候標成「不新鮮」，正是為了這件事。
"""

import logging
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.enums import DataSource
from app.models.market import MarketBar
from app.models.mixins import utcnow
from app.services.market_data.base import Bar, Timeframe

logger = logging.getLogger("app.bar_store")

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


# 一段序列多久沒有被抓過，就算是「沒有人再看了」。
#
# **判準是「被寫進來的時間」（`fetched_at`），不是「那根 K 棒是什麼時候的」（`ts`）。**
# 一段還在被盯的序列，每收一根新的就多一列新的 fetched_at；一段沒有人再看的，最新那
# 一列就停在他最後一次打開那張圖的時候。
#
# 而且判準是**整段**的最新值，不是逐列。逐列的話會把還在用的序列的歷史刪掉——那些舊
# 列本來就是很久以前寫進去的，而它們正是這些資料存在的全部理由（上游掛掉的時候圖表
# 還有底可以墊）。
#
# 90 天：最長的週期是 1mo，也就是一段還在被盯的月線序列最多可能 31 天才多一列。三根
# 月線的餘裕。訂太短的代價是他每個月都會掉一次底，而症狀是上游抖一下那張圖就空了。
IDLE_SERIES_DAYS = 90

# 掃描要花錢（這張表沒有 fetched_at 的索引），而它要回收的東西是以「月」為單位長出來
# 的，所以一天問一次遠遠夠用。
#
# 記在行程裡而不是資料庫裡：多掃幾次是安全方向（免費方案休眠、重新部署都會讓它重
# 來），而為了記帳多開一張表，就是為了省空間而多佔空間。
_SWEEP_EVERY_SEC = 24 * 3600.0
_last_sweep_at: float | None = None


def _idle_series(db: Session, cutoff: datetime) -> list[tuple]:
    """哪幾段序列的最新一列比 `cutoff` 還舊。"""
    return list(
        db.execute(
            select(MarketBar.data_source, MarketBar.symbol, MarketBar.timeframe)
            .group_by(MarketBar.data_source, MarketBar.symbol, MarketBar.timeframe)
            .having(func.max(MarketBar.fetched_at) < cutoff)
        ).all()
    )


def sweep_idle_series(db: Session) -> int:
    """把沒有人再看的那幾段整段丟掉，回傳丟了幾段。

    ＊ 為什麼是「整段一次一個 DELETE」而不是一句 SQL。

    一句 `WHERE (a, b, c) IN (SELECT ...)` 的 row-value 語法 SQLite 和 Postgres 支援
    的程度不一樣，而這個 app 兩邊都要跑（開發機 SQLite、線上 Postgres）。閒置的段數本
    來就少，一段一句是可讀而且可攜的。

    **絕不拋出。** 呼叫端是 `save`，而 `save` 跑在使用者正在等的那個 HTTP 請求裡，也
    跑在盯盤迴圈的一輪裡。回收空間是次要的，存下這一批 K 棒是主要的。
    """
    dropped = 0
    try:
        cutoff = utcnow() - timedelta(days=IDLE_SERIES_DAYS)
        for data_source, symbol, timeframe in _idle_series(db, cutoff):
            db.execute(
                delete(MarketBar).where(
                    MarketBar.data_source == data_source,
                    MarketBar.symbol == symbol,
                    MarketBar.timeframe == timeframe,
                )
            )
            dropped += 1
        if dropped:
            db.flush()
            logger.info("回收了 %s 段沒有人再看的 K 棒序列", dropped)
    except Exception:
        logger.exception("回收閒置的 K 棒序列失敗；這一批 K 棒照樣存下來")
    return dropped


def _sweep_if_due(db: Session) -> None:
    """一天最多一次。`save` 每一輪都會被呼叫，掃描不可以跟著它跑。"""
    global _last_sweep_at
    now = time.monotonic()
    if _last_sweep_at is not None and now - _last_sweep_at < _SWEEP_EVERY_SEC:
        return
    _last_sweep_at = now
    sweep_idle_series(db)


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
    # 上面那句封住「這一段有多深」，這一句封住「總共有幾段」（#61）。同一條路，同一
    # 個理由——只是它一天才問一次，因為它要回收的東西是以月為單位長出來的。
    _sweep_if_due(db)
