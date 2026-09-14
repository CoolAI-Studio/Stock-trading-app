"""收盤摘要：收盤之後，一個市場一天一則，而且只說真的發生過的事（#117）。

＊ 規格（ONBOARDING.md 方案 2）。

他填幾支股票 → 加進自選股 ＋「收盤後把當日漲跌整理成一則通知」。給不想被盤中打擾的人。

＊ 為什麼用報價，不用日 K。

`market_data.base.closed_bars` 刻意要等**那一天結束**才放出日 K（Yahoo 收盤後還會調整那
一根），所以收盤後的時間窗裡，今天那根日 K 永遠拿不到。報價的 `quote_time` 是交易所給的
成交時間，`fetched_at` 是上游真的回答的那一刻——兩個一起看，才說得出「這是今天的收盤」。

＊ 釘在這裡的每一條，都是一種會送出假話或燒掉預算的方式：

- **時間窗外不碰資料庫。** 這支函式掛在盯盤迴圈的每一輪上，而每一句 SQL 都是 Neon 的預算
  （#98 起）。窗外 0 句，由 `counted` 數。
- **假日被市場日曆當成有開**（寧可多醒，不可漏看），所以「今天有沒有開」要由成交時間回答；
  不然休市日會把昨天的收盤講成今天的。
- **盤中快取的價格不是收盤價。** 上游刷新失敗時，服務會回一個舊的報價。
- **同一天不重送、關掉就不送、那個市場沒有自選就不送。**
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.enums import DataSource
from app.models.daily_summary import DailySummary
from app.models.user import User
from app.models.watchlist import WatchlistItem
from app.services import daily_summary, market_calendar, symbol_search
from app.services.events import Event
from app.services.market_data.base import Quote
from app.services.notification import dispatcher

TAIPEI = ZoneInfo("Asia/Taipei")
NEW_YORK = ZoneInfo("America/New_York")

# 2026-09-15 是星期二。
TW_CLOSE = datetime(2026, 9, 15, 13, 30, tzinfo=TAIPEI)
US_CLOSE = datetime(2026, 9, 15, 16, 0, tzinfo=NEW_YORK)


def _taipei(hour: int, minute: int = 0, day: int = 15) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=TAIPEI).astimezone(UTC)


class FakeQuotes:
    """只回答被準備好的報價，並記下被問了什麼。"""

    def __init__(self, quotes: dict[str, Quote] | None = None):
        self.quotes = quotes or {}
        self.asked: list[tuple[tuple[str, ...], DataSource]] = []

    def get_quotes(self, symbols: list[str], data_source: DataSource) -> dict[str, Quote]:
        self.asked.append((tuple(symbols), data_source))
        return {symbol: self.quotes[symbol] for symbol in symbols if symbol in self.quotes}


def _quote(
    symbol: str,
    price: str,
    prev_close: str,
    *,
    traded_at: datetime = TW_CLOSE,
    answered_at: datetime | None = None,
    data_source: DataSource = DataSource.YFINANCE,
) -> Quote:
    price_dec, prev_dec = Decimal(price), Decimal(prev_close)
    return Quote(
        symbol=symbol,
        data_source=data_source,
        price=price_dec,
        prev_close=prev_dec,
        change_pct=(price_dec - prev_dec) / prev_dec * 100,
        quote_time=traded_at.astimezone(UTC),
        fetched_at=(answered_at or traded_at.replace(minute=45)).astimezone(UTC),
    )


@pytest.fixture
def owner(db_session) -> User:
    user = User(email="summary@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _watch(db_session, owner: User, *symbols: str, source: DataSource = DataSource.YFINANCE):
    for symbol in symbols:
        db_session.add(WatchlistItem(user_id=owner.id, symbol=symbol, data_source=source))
    db_session.commit()


def _switch_on(db_session, owner: User) -> DailySummary:
    summary = DailySummary(user_id=owner.id, is_enabled=True)
    db_session.add(summary)
    db_session.commit()
    db_session.refresh(summary)
    return summary


def _tw_quotes() -> FakeQuotes:
    return FakeQuotes(
        {
            "2330.TW": _quote("2330.TW", "1050", "1035"),
            "2454.TW": _quote("2454.TW", "1190", "1200"),
        }
    )


# --- 時間窗外：什麼都不碰 ---------------------------------------------------------


@pytest.mark.parametrize(
    "moment",
    [
        _taipei(11, 0),  # 盤中
        _taipei(13, 35),  # 剛收盤，還不到 10 分鐘——收盤價可能還沒定
        _taipei(20, 0),  # 台股的窗已經過了，美股還沒開
        datetime(2026, 9, 19, 14, 0, tzinfo=TAIPEI).astimezone(UTC),  # 星期六
    ],
)
def test_outside_the_window_it_touches_nothing(db_session, counted, owner, moment):
    _watch(db_session, owner, "2330.TW", "AAPL")
    _switch_on(db_session, owner)
    quotes = _tw_quotes()
    counted.clear()

    events = daily_summary.run_due(db_session, now=moment, service=quotes)

    assert events == []
    assert counted == [], f"時間窗外送出了 {len(counted)} 句 SQL——這支函式每一輪都會被呼叫"
    assert quotes.asked == []


# --- 台股收盤之後 ------------------------------------------------------------------


def test_after_the_taiwan_close_one_summary_with_only_taiwanese_symbols(db_session, owner):
    _watch(db_session, owner, "2330.TW", "AAPL", "2454.TW")
    _switch_on(db_session, owner)

    events = daily_summary.run_due(db_session, now=_taipei(13, 50), service=_tw_quotes())

    assert len(events) == 1
    event = events[0]
    assert event.type == "summary.daily"
    assert event.data["user_id"] == owner.id
    assert event.data["market"] == "tw"
    assert event.data["day"] == "2026-09-15"
    # 自選股的順序：他把在意的那一檔放第一個。
    assert [line["symbol"] for line in event.data["lines"]] == ["2330.TW", "2454.TW"]
    assert event.data["missing"] == []


def test_a_taiwanese_line_carries_the_name_he_would_recognise(db_session, owner):
    _watch(db_session, owner, "2330.TW")
    _switch_on(db_session, owner)

    [event] = daily_summary.run_due(db_session, now=_taipei(13, 50), service=_tw_quotes())

    listing = symbol_search.listing_for("2330.TW")
    assert event.data["lines"][0]["name"] == (listing or {}).get("short_name")


def test_it_is_not_sent_twice_on_the_same_day(db_session, owner):
    _watch(db_session, owner, "2330.TW")
    _switch_on(db_session, owner)
    quotes = _tw_quotes()

    first = daily_summary.run_due(db_session, now=_taipei(13, 50), service=quotes)
    second = daily_summary.run_due(db_session, now=_taipei(14, 20), service=quotes)

    assert len(first) == 1
    assert second == []


def test_the_next_trading_day_gets_its_own(db_session, owner):
    _watch(db_session, owner, "2330.TW")
    _switch_on(db_session, owner)
    tuesday = _tw_quotes()
    wednesday_close = datetime(2026, 9, 16, 13, 30, tzinfo=TAIPEI)
    wednesday = FakeQuotes(
        {"2330.TW": _quote("2330.TW", "1060", "1050", traded_at=wednesday_close)}
    )

    daily_summary.run_due(db_session, now=_taipei(13, 50), service=tuesday)
    events = daily_summary.run_due(db_session, now=_taipei(13, 50, day=16), service=wednesday)

    assert len(events) == 1
    assert events[0].data["day"] == "2026-09-16"


# --- 不說假話 ----------------------------------------------------------------------


def test_a_holiday_does_not_repeat_yesterdays_close_as_today(db_session, owner):
    """市場日曆把假日當成有開（寧可多醒）。所以今天有沒有開，要由成交時間回答。"""
    _watch(db_session, owner, "2330.TW")
    summary = _switch_on(db_session, owner)
    monday_close = datetime(2026, 9, 14, 13, 30, tzinfo=TAIPEI)
    quotes = FakeQuotes(
        {
            "2330.TW": _quote(
                "2330.TW",
                "1035",
                "1020",
                traded_at=monday_close,
                answered_at=TW_CLOSE.replace(minute=45),
            )
        }
    )

    events = daily_summary.run_due(db_session, now=_taipei(15, 0), service=quotes)

    assert events == []
    db_session.refresh(summary)
    assert summary.tw_done_on is None, "沒有送出去的那一天不可以被記成已經處理過"
    assert "今天" in (summary.last_error or "")


def test_a_price_from_before_the_close_is_not_a_close(db_session, owner):
    """上游刷新失敗時，服務會回一個盤中留下來的報價。那不是收盤價。"""
    _watch(db_session, owner, "2330.TW")
    _switch_on(db_session, owner)
    quotes = FakeQuotes(
        {
            "2330.TW": _quote(
                "2330.TW", "1041", "1035", traded_at=_taipei(12, 0), answered_at=_taipei(12, 1)
            )
        }
    )

    events = daily_summary.run_due(db_session, now=_taipei(15, 0), service=quotes)

    assert events == []


def test_when_one_symbol_is_late_it_waits_then_sends_what_it_has(db_session, owner):
    """收盤後剛開始的那一段，上游常常還沒全部更新。等一下；等太久就照有的送，並說出缺了誰。"""
    _watch(db_session, owner, "2330.TW", "2454.TW")
    _switch_on(db_session, owner)
    quotes = FakeQuotes({"2330.TW": _quote("2330.TW", "1050", "1035")})

    early = daily_summary.run_due(db_session, now=_taipei(13, 50), service=quotes)
    late = daily_summary.run_due(db_session, now=_taipei(14, 45), service=quotes)

    assert early == []
    assert len(late) == 1
    assert [line["symbol"] for line in late[0].data["lines"]] == ["2330.TW"]
    assert late[0].data["missing"] == ["2454.TW"]


# --- 不該送的時候 --------------------------------------------------------------------


def test_switched_off_means_nothing_and_asks_nobody(db_session, owner):
    _watch(db_session, owner, "2330.TW")
    db_session.add(DailySummary(user_id=owner.id, is_enabled=False))
    db_session.commit()
    quotes = _tw_quotes()

    assert daily_summary.run_due(db_session, now=_taipei(13, 50), service=quotes) == []
    assert quotes.asked == []


def test_no_watched_symbol_in_that_market_means_no_summary_for_it(db_session, owner):
    _watch(db_session, owner, "AAPL")
    _switch_on(db_session, owner)
    quotes = _tw_quotes()

    assert daily_summary.run_due(db_session, now=_taipei(13, 50), service=quotes) == []
    assert quotes.asked == [], "台股的時間窗不該去問一支美股"


def test_crypto_never_closes_so_it_is_never_in_a_summary(db_session, owner):
    _watch(db_session, owner, "BTCUSDT", source=DataSource.BINANCE)
    _switch_on(db_session, owner)

    assert market_calendar.market_of("BTCUSDT", DataSource.BINANCE) is None
    assert daily_summary.run_due(db_session, now=_taipei(13, 50), service=FakeQuotes()) == []


# --- 美股 --------------------------------------------------------------------------


def test_the_us_summary_comes_after_the_new_york_close(db_session, owner):
    _watch(db_session, owner, "2330.TW", "AAPL")
    _switch_on(db_session, owner)
    quotes = FakeQuotes({"AAPL": _quote("AAPL", "231.5", "229", traded_at=US_CLOSE)})
    moment = datetime(2026, 9, 15, 16, 20, tzinfo=NEW_YORK).astimezone(UTC)

    [event] = daily_summary.run_due(db_session, now=moment, service=quotes)

    assert event.data["market"] == "us"
    assert event.data["day"] == "2026-09-15"
    assert [line["symbol"] for line in event.data["lines"]] == ["AAPL"]


# --- 手機上看到的那一行 -------------------------------------------------------------


def _summary_event(**overrides) -> Event:
    data = {
        "user_id": 1,
        "market": "tw",
        "label": "台股",
        "day": "2026-09-15",
        "lines": [
            {
                "symbol": "2330.TW",
                "name": "台積電",
                "price": "1050",
                "change": "15",
                "change_pct": "1.4493",
            },
            {
                "symbol": "2454.TW",
                "name": "聯發科",
                "price": "1190",
                "change": "-10",
                "change_pct": "-0.8333",
            },
        ],
        "missing": ["0050.TW"],
    }
    data.update(overrides)
    return Event(type="summary.daily", data=data)


def test_the_dispatcher_delivers_a_summary():
    assert "summary.daily" in dispatcher._DISPATCHED_EVENT_TYPES


def test_the_message_says_which_market_which_day_and_what_moved():
    message = dispatcher._format_message(_summary_event(), None)

    assert "台股收盤摘要" in message
    assert "9/15" in message
    assert "台積電" in message and "2330.TW" in message
    assert "▲" in message and "+1.45%" in message
    assert "▼" in message and "-0.83%" in message


def test_the_message_names_what_it_could_not_get():
    """少了一檔卻不說，他會以為那一檔今天沒有動。"""
    message = dispatcher._format_message(_summary_event(), None)

    assert "0050.TW" in message
    assert "拿不到" in message


def test_the_message_says_it_is_not_an_order():
    message = dispatcher._format_message(_summary_event(missing=[]), None)

    assert "不會" in message and "下單" in message


def test_the_trading_day_is_reported_in_the_markets_own_date():
    """美股收盤是台北時間隔天凌晨。摘要要說的是紐約的那一天，不是伺服器或他所在的日期。"""
    assert date.fromisoformat(_summary_event(day="2026-09-15").data["day"]) == date(2026, 9, 15)


# --- 迴圈真的會把它送出去 ------------------------------------------------------------


def test_the_loop_publishes_what_the_summary_finds(db_session, published_events, monkeypatch):
    """寫好了卻沒有接進迴圈，是這種功能最安靜的壞法：每一條測試都綠，而他一則都收不到。"""
    from app.services import market_loop
    from app.services.market_data.providers.mock_provider import MockProvider
    from app.services.market_data.service import MarketDataService

    wanted = Event(type="summary.daily", data={"user_id": 1, "market": "tw"})
    monkeypatch.setattr(daily_summary, "run_due", lambda session, service=None: [wanted])

    market_loop.tick_once(
        db=db_session,
        market_data_service=MarketDataService(
            providers={DataSource.YFINANCE: MockProvider(base_prices={})}
        ),
    )

    assert wanted in published_events


def test_a_broken_summary_does_not_take_the_loop_down(db_session, published_events, monkeypatch):
    """摘要是加分的東西。它炸掉的那一輪，停損和策略提醒照樣要跑完、照樣要發出去。"""
    from app.services import market_loop
    from app.services.market_data.providers.mock_provider import MockProvider
    from app.services.market_data.service import MarketDataService

    def explode(session, service=None):
        raise RuntimeError("upstream fell over")

    monkeypatch.setattr(daily_summary, "run_due", explode)

    events = market_loop.tick_once(
        db=db_session,
        market_data_service=MarketDataService(
            providers={DataSource.YFINANCE: MockProvider(base_prices={})}
        ),
    )

    assert isinstance(events, list)
