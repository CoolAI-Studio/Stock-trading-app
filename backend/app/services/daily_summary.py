"""收盤摘要：收盤之後，一個市場一天一則（ONBOARDING.md 方案 2，#117）。

給不想被盤中打擾的人。清單就是他的自選股；每個有收盤的市場（台股、美股）在收盤後送一則，
說每一檔當天漲跌多少。

＊ 掛在盯盤迴圈的每一輪上，所以**時間窗外一句 SQL 都不送**。

`run_due` 第一件事是算「現在是不是某個市場收盤後的時間窗」，那一步只看時鐘。窗外直接回
來——這支迴圈的每一句 SQL 都是 Neon 免費方案的預算（CLAUDE.md #98 起那一整節），而一天
裡絕大部分的輪次都在窗外。

＊ 用報價，不用日 K。

`market_data.base.closed_bars` 刻意要等**那一天結束**才放出日 K（Yahoo 收盤後還會調整那
一根），所以收盤後的時間窗裡今天的日 K 拿不到。報價的 `quote_time` 是交易所給的成交時間，
`fetched_at` 是上游真的回答的那一刻，兩個一起看才說得出「這是今天的收盤」：

- 成交時間不是今天（當地日期）→ 今天沒有開。市場日曆把假日當成有開（寧可多醒），所以這
  一條是唯一擋得住「休市日把昨天的收盤講成今天的」的地方。
- 回答時間在收盤之前 → 那是上游刷新失敗時服務留下來的盤中價，不是收盤價。

＊ 等一下，但不要等到沒有。

收盤後剛開始的那一段，上游常常還沒全部更新。有代號缺的時候先不送；收盤一小時之後就照拿得
到的送，並且說出缺了誰——少了一檔卻不說，他會以為那一檔今天沒有動。一檔都拿不到就不送，把
原因記在那一列上，時間窗內會再試。

＊ 加密貨幣不在摘要裡：它不收盤，沒有「收盤」可以整理。畫面上要明說（`state_for` 的
`unsupported`）。
"""

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.enums import DataSource
from app.models.daily_summary import DailySummary
from app.models.mixins import utcnow
from app.models.watchlist import WatchlistItem
from app.services import market_calendar, symbol_search
from app.services.events import Event
from app.services.market_data.base import Quote

logger = logging.getLogger("app.daily_summary")

EVENT_TYPE = "summary.daily"

# 收盤後這麼久才開始問：收盤那一刻上游常常還停在最後一筆盤中成交。
NOT_BEFORE = timedelta(minutes=10)
# 有代號還沒拿到的時候，等到收盤後這麼久才照有的送。
PARTIAL_AFTER = timedelta(hours=1)
# 時間窗的尾巴。過了還沒有今天的資料，這一天就不送了——多半是休市日。
WINDOW = timedelta(hours=6)


def _aware(moment: datetime) -> datetime:
    # SQLite 存回來、或手工建的時間可能是 naive 的；這個 app 存的一律是 UTC。
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


def _done_attr(market: str) -> str:
    return f"{market}_done_on"


def due_markets(moment: datetime) -> dict[str, date]:
    """每一個正處於收盤後時間窗的市場 → 它當地的那個交易日。**不碰資料庫。**"""
    moment = _aware(moment)
    due: dict[str, date] = {}
    for market in market_calendar.MARKET_LABELS:
        day = market_calendar.local_date(market, moment)
        if day.weekday() >= 5:
            continue
        closes = market_calendar.close_on(market, day)
        if closes + NOT_BEFORE <= moment < closes + WINDOW:
            due[market] = day
    return due


def _todays_close(quote: Quote | None, market: str, day: date) -> Quote | None:
    """這個報價是不是「這個市場今天的收盤」。理由在檔頭。"""
    if quote is None or quote.quote_time is None or quote.fetched_at is None:
        return None
    if market_calendar.local_date(market, _aware(quote.quote_time)) != day:
        return None
    if _aware(quote.fetched_at) < market_calendar.close_on(market, day):
        return None
    return quote


def _line(item: WatchlistItem, quote: Quote) -> dict:
    change = quote.price - quote.prev_close if quote.prev_close is not None else None
    # 台股的代號對他來說是一串數字，名字才是他認得的東西。美股代號本身就是名字。
    listing = symbol_search.listing_for(item.symbol)
    return {
        "symbol": item.symbol,
        "name": (listing or {}).get("short_name"),
        "price": str(quote.price),
        "change": str(change) if change is not None else None,
        "change_pct": str(quote.change_pct) if quote.change_pct is not None else None,
    }


def _summarise(
    summary: DailySummary,
    market: str,
    day: date,
    items: list[WatchlistItem],
    moment: datetime,
    service,
) -> Event | None:
    by_source: dict[DataSource, list[str]] = {}
    for item in items:
        by_source.setdefault(item.data_source, []).append(item.symbol)

    quotes: dict[str, Quote] = {}
    for source, symbols in by_source.items():
        try:
            quotes.update(service.get_quotes(symbols, source))
        except Exception:
            # 抓不到就當作這幾檔今天缺資料：下面會決定要等還是照有的送。
            logger.exception("收盤摘要：%s 的報價抓不到", market)

    lines: list[dict] = []
    missing: list[str] = []
    for item in items:
        quote = _todays_close(quotes.get(item.symbol), market, day)
        if quote is None:
            missing.append(item.symbol)
        else:
            lines.append(_line(item, quote))

    label = market_calendar.MARKET_LABELS[market]
    if not lines:
        summary.last_error = (
            f"{label}今天（{day.month}/{day.day}）還拿不到任何一檔的收盤資料——"
            "可能是休市日，或上游還沒更新。收盤後六小時內會再試。"
        )
        return None
    if missing and moment < market_calendar.close_on(market, day) + PARTIAL_AFTER:
        return None

    setattr(summary, _done_attr(market), day)
    summary.last_sent_at = utcnow()
    summary.last_error = None
    return Event(
        type=EVENT_TYPE,
        data={
            "user_id": summary.user_id,
            "market": market,
            "label": label,
            "day": day.isoformat(),
            "lines": lines,
            "missing": missing,
        },
    )


def run_due(db: Session, now: datetime | None = None, service=None) -> list[Event]:
    """這一刻該送的收盤摘要，當成事件回傳（由呼叫的人發出去）。"""
    moment = _aware(now or datetime.now(UTC))
    markets = due_markets(moment)
    if not markets:
        # 這一行就是整支函式的預算。
        return []

    summaries = db.query(DailySummary).filter(DailySummary.is_enabled.is_(True)).all()
    if not summaries:
        return []

    if service is None:
        # 在這裡才載入：行情服務很重，而絕大多數輪次根本走不到這一行。
        from app.services.market_data.service import get_market_data_service

        service = get_market_data_service()

    events: list[Event] = []
    for summary in summaries:
        watched = (
            db.query(WatchlistItem)
            .filter(WatchlistItem.user_id == summary.user_id)
            .order_by(WatchlistItem.id)
            .all()
        )
        for market, day in markets.items():
            if getattr(summary, _done_attr(market)) == day:
                continue
            items = [
                item
                for item in watched
                if market_calendar.market_of(item.symbol, item.data_source) == market
            ]
            if not items:
                continue
            event = _summarise(summary, market, day, items, moment, service)
            if event is not None:
                events.append(event)
        db.commit()
    return events


# --- 手機上看到的那一則 -------------------------------------------------------------


def _number(raw) -> str:
    try:
        value = Decimal(str(raw)).normalize()
    except (InvalidOperation, ValueError):
        return str(raw)
    return f"{value:,f}"


def _move(change, change_pct) -> str:
    if change is None and change_pct is None:
        return ""
    try:
        delta = Decimal(str(change)) if change is not None else None
    except (InvalidOperation, ValueError):
        delta = None
    parts: list[str] = []
    if delta is not None:
        arrow = "▲" if delta > 0 else "▼" if delta < 0 else "平"
        parts.append(arrow if delta == 0 else f"{arrow}{_number(abs(delta))}")
    if change_pct is not None:
        try:
            parts.append(f"{Decimal(str(change_pct)):+.2f}%")
        except (InvalidOperation, ValueError):
            pass
    return f"（{'，'.join(parts)}）" if parts else ""


def format_message(data: dict) -> str:
    label = data.get("label") or "收盤"
    try:
        day = date.fromisoformat(str(data.get("day")))
        heading = f"{label}收盤摘要（{day.month}/{day.day}）"
    except ValueError:
        heading = f"{label}收盤摘要"

    rows = [heading]
    for line in data.get("lines") or []:
        who = f"{line['name']} {line['symbol']}" if line.get("name") else line["symbol"]
        price = _number(line.get("price"))
        move = _move(line.get("change"), line.get("change_pct"))
        rows.append(f"{who}　{price}{move}")
    missing = data.get("missing") or []
    if missing:
        rows.append(f"拿不到今天收盤資料的：{'、'.join(missing)}")
    rows.append("這是收盤後的整理，不會幫你下單。")
    return "\n".join(rows)


# --- 畫面上的那一格 -----------------------------------------------------------------


def state_for(db: Session, user_id: int) -> dict:
    summary = db.query(DailySummary).filter(DailySummary.user_id == user_id).first()
    watched = (
        db.query(WatchlistItem)
        .filter(WatchlistItem.user_id == user_id)
        .order_by(WatchlistItem.id)
        .all()
    )
    placed = [(item, market_calendar.market_of(item.symbol, item.data_source)) for item in watched]
    return {
        "is_enabled": bool(summary and summary.is_enabled),
        "markets": [
            {
                "market": market,
                "label": label,
                "symbols": [item.symbol for item, where in placed if where == market],
                "done_on": getattr(summary, _done_attr(market)) if summary else None,
            }
            for market, label in market_calendar.MARKET_LABELS.items()
        ],
        "unsupported": [item.symbol for item, where in placed if where is None],
        "last_sent_at": summary.last_sent_at if summary else None,
        "last_error": summary.last_error if summary else None,
    }


def set_enabled(db: Session, user_id: int, enabled: bool) -> None:
    summary = db.query(DailySummary).filter(DailySummary.user_id == user_id).first()
    if summary is None:
        summary = DailySummary(user_id=user_id)
        db.add(summary)
    summary.is_enabled = enabled
    db.commit()
