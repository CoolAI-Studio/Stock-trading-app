"""存下來的 K 棒要有上限——它是這個 app 裡唯一一張完全沒有上限的表。

其他每一張會長大的表都有人在管：策略版本有 `STRATEGY_VERSION_LIMIT`（丟最舊的，
但現在在跑的那一版不丟），回測紀錄有 `MAX_RUNS_PER_USER`，webhook 稽核有天數＋列
數兩道，通知紀錄有一顆「清空」按鈕。`market_bars` 兩樣都沒有，而且整個 repo 沒有
任何端點、腳本或排程刪得掉它。

今天寫進去的人只有圖表端點（`GET /api/market/bars`，全部 `get_bars()` 呼叫端
裡只有它傳 `db=`）：他每看一次圖就寫一次，而每一個（來源、代號、週期）都是一
段會一直往前滑的序列。盯盤迴圈現在一列都不寫——但 #59 要補的正是那個 `db=`，
一補上去一支 1 分鐘的 on_bar 策略就是每個交易日約 390 根、一年十萬根。這道上
限先落地，那條路才能打開。

更關鍵的是讀回去的那條路（`market_data/service.py` 的 `_prime_from_storage`）
**永遠只讀最新的 `MAX_STORED_BARS` 根**：超過那個深度的每一列，都是沒有任何一條
路讀得到的資料，只是佔著空間。

空間用完的後果不是「圖變醜」。免費方案是整個資料庫 0.5GB，塞滿之後失敗的是**寫
入**——通知紀錄、重送佇列、部位，全部一起。警告不能停擺是這個產品的最高優先，一
張「抓不到時用來墊底」的圖表資料，不值得為它把整個 app 停掉。而使用者不是工程
師：他手上沒有 psql，app 裡也沒有任何一顆按鈕能把空間拿回來。

所以上限走跟其他表同一條路——**在寫的那一刻自己修剪，丟最舊的**。不需要新的排
程、不需要新的端點，也不需要他做任何事。

它封住的是「每一段序列的深度」，不是「序列的數量」——看過一次就不再更新的那些
段依然永遠躺在那裡，見 #61。不要把這一組測試讀成「這張表現在有上限了」。

這裡的斷言刻意對著 `MAX_STORED_BARS`（讀得回來的最大深度）而不是一個新常數的名
字：要守的性質是「存下去的不可以多過讀得回來的」，而不是「某個 dict 裡寫著
1000」。
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import event, func, select

from app.enums import DataSource
from app.models.market import MarketBar
from app.services import bar_store
from app.services.market_data.base import Bar, Timeframe
from app.services.market_data.service import MAX_STORED_BARS

_START = datetime(2026, 3, 2, tzinfo=UTC)


def _bars(
    count: int,
    *,
    start_index: int = 0,
    symbol: str = "2330.TW",
    timeframe: Timeframe = Timeframe.MINUTE_1,
) -> list[Bar]:
    """連續的 K 棒。close 帶著序號，好認出留下來的到底是哪幾根。"""
    return [
        Bar(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=_START + timedelta(minutes=start_index + i),
            open=100.0,
            high=101.0,
            low=99.0,
            close=float(start_index + i),
            volume=1000.0,
        )
        for i in range(count)
    ]


def _row_count(db, symbol: str, timeframe: Timeframe) -> int:
    return db.execute(
        select(func.count())
        .select_from(MarketBar)
        .where(
            MarketBar.data_source == DataSource.YFINANCE,
            MarketBar.symbol == symbol,
            MarketBar.timeframe == timeframe.value,
        )
    ).scalar_one()


def test_one_series_does_not_grow_without_a_ceiling(db_session):
    """一段序列存不下比「讀得回來的深度」更多的列。

    多出來的那幾列不是「多存一點以防萬一」——沒有任何一條路讀得到它們，它們只是在
    一個 0.5GB 的資料庫上佔位子。
    """
    bar_store.save(
        db_session,
        DataSource.YFINANCE,
        "2330.TW",
        Timeframe.MINUTE_1,
        _bars(MAX_STORED_BARS + 200),
    )

    assert _row_count(db_session, "2330.TW", Timeframe.MINUTE_1) <= MAX_STORED_BARS


def test_the_ceiling_holds_when_bars_arrive_a_few_at_a_time(db_session):
    """真實的形狀是一次幾根、一直加，不是一次塞爆。

    盯盤迴圈每個週期抓一次，每次只多出幾根新的。上限如果只在「一次寫很多」的時候
    才生效，線上那條每天長幾百根的路就永遠繞過它——而那條才是會把資料庫塞滿的路。
    """
    for chunk in range(0, MAX_STORED_BARS + 300, 100):
        bar_store.save(
            db_session,
            DataSource.YFINANCE,
            "2330.TW",
            Timeframe.MINUTE_1,
            _bars(100, start_index=chunk),
        )

    assert _row_count(db_session, "2330.TW", Timeframe.MINUTE_1) <= MAX_STORED_BARS


def test_what_is_dropped_is_the_oldest(db_session):
    """丟掉的是最舊的那幾根，不是最新的。

    跟策略版本同一條規則：他要看的是「現在」，圖表和指標暖身讀的都是最近那一段。
    反過來丟最新的，等於留著一段沒有人會看的歷史，然後把今天的圖弄不見。

    問的是**表裡真的還剩下哪幾根**，不是 `load()` 回什麼——`load()` 本來就由新往舊
    取，所以拿它來問，會在完全沒有修剪的時候也答對。
    """
    bar_store.save(
        db_session,
        DataSource.YFINANCE,
        "2330.TW",
        Timeframe.MINUTE_1,
        _bars(MAX_STORED_BARS + 200),
    )

    where = (
        MarketBar.data_source == DataSource.YFINANCE,
        MarketBar.symbol == "2330.TW",
        MarketBar.timeframe == Timeframe.MINUTE_1.value,
    )
    oldest = db_session.execute(select(func.min(MarketBar.close)).where(*where)).scalar_one()
    newest = db_session.execute(select(func.max(MarketBar.close)).where(*where)).scalar_one()

    assert float(oldest) == 200.0, "留下來的最舊那一根不對：被丟掉的不是最舊的"
    assert float(newest) == float(MAX_STORED_BARS + 199), "最新那一根被丟掉了"


def test_the_ceiling_is_counted_per_series(db_session):
    """上限是一段序列各算各的，不是整張表一個數字。

    鍵是（來源、代號、週期）三個一起。修剪如果漏掉其中一個條件，它就會在存台積電
    的分線時把蘋果的日線刪掉——那是一個不會報錯、只會讓另一支的圖突然變空的錯。
    """
    bar_store.save(
        db_session,
        DataSource.YFINANCE,
        "AAPL",
        Timeframe.DAY_1,
        _bars(5, symbol="AAPL", timeframe=Timeframe.DAY_1),
    )
    bar_store.save(
        db_session,
        DataSource.YFINANCE,
        "2330.TW",
        Timeframe.MINUTE_1,
        _bars(MAX_STORED_BARS + 200),
    )

    assert _row_count(db_session, "AAPL", Timeframe.DAY_1) == 5


def test_a_deep_chart_request_does_not_rewrite_the_same_rows_every_time(db_session):
    """同一段深歷史存第二次，不可以再插一次列。

    圖表往前拉的深度是倍增的（`PriceChart` 的 depth：300 → 600 → 1200 → 2400…，一
    小時線上限 3500），所以超過上限是常態不是例外。如果 `save()` 把拿到的全部寫進
    去、再讓 `_trim` 刪掉多的，那**每一次**拉深的請求都會插一批再刪同一批，淨值永遠
    是零——為了省空間換來永久的寫入放大，而且發生在使用者正在等的那個 HTTP 請求裡，
    在免費 Postgres 上還一路產 dead tuple。

    所以要先切輸入再寫。這條測試數的是真的 SQL：上面那四條全部只看最後的列數，切
    不切輸入它們都綠，所以把 `bars = bars[-MAX_STORED_BARS:]` 拿掉不會有任何東西變
    紅——除了這一條。
    """
    deep = _bars(MAX_STORED_BARS + 1400)
    bar_store.save(db_session, DataSource.YFINANCE, "2330.TW", Timeframe.MINUTE_1, deep)

    touched: list[tuple[str, int]] = []

    @event.listens_for(db_session.get_bind(), "after_cursor_execute")
    def _count(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        verb = statement.strip().split(None, 1)[0].upper()
        if verb in ("INSERT", "DELETE"):
            # 數的是**真的動到的列**，不是發了幾句 SQL。`_trim` 每次都會發一句
            # DELETE，而清完之後那一句刪到零列（走 PK 的範圍掃描，不產 dead
            # tuple）——那不是寫入放大。要守的是「同一批列被反覆插了又刪」。
            touched.append((verb, cursor.rowcount))

    try:
        # 使用者又拉了一次同樣深的圖。手上的 K 棒一模一樣，所以正確的答案是「什麼都
        # 不用做」。
        bar_store.save(db_session, DataSource.YFINANCE, "2330.TW", Timeframe.MINUTE_1, deep)
    finally:
        event.remove(db_session.get_bind(), "after_cursor_execute", _count)

    moved = [(verb, rows) for verb, rows in touched if rows > 0]
    assert moved == [], (
        f"同一段序列存第二次還在搬列：{moved}。這表示 save() 先把超過上限的部分寫進去、"
        "再由 _trim 刪掉——每一次拉深的圖表請求都插一批再刪同一批，淨值為零，永遠。"
    )
