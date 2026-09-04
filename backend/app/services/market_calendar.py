"""When a symbol's market is open.

Nothing in the app knew this, and everything expensive about it happened at
night: one symbol polled ~5,760 times a day against a scraper that blocks IPs
for exactly that; on_tick strategies fed the same closing price thousands of
times until their internal averages meant nothing; the stop-loss scan filing a
pending SELL at 3am off a stale close, watching it expire 180 minutes later,
and filing another.

**The rule this module is built on: only say 閉市 when that is certain.**
Weekends and clock times outside the session are certain. Holidays are not,
and are deliberately treated as open. The two mistakes are not symmetrical --
polling a shut market wastes requests, while skipping a real trading day means
the owner does not get told about the trade, which is this product's one
unaffordable failure.

Holidays are therefore a known, accepted gap. Taiwan's follow the lunar
calendar and move every year, so a hand-maintained table would be wrong within
months and wrong in the dangerous direction. Doing it properly means a real
calendar dependency (exchange_calendars); until then a public holiday costs a
day of pointless requests and nothing worse.

Stdlib only -- zoneinfo, no new dependency for what is a table of two markets.
"""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.enums import DataSource
from app.services import symbol_search


class _Session:
    __slots__ = ("tz", "opens", "closes")

    def __init__(self, tz: str, opens: time, closes: time) -> None:
        self.tz = ZoneInfo(tz)
        self.opens = opens
        self.closes = closes

    def is_open_at(self, moment: datetime) -> bool:
        local = moment.astimezone(self.tz)
        if local.weekday() >= 5:  # Saturday, Sunday
            return False
        return self.opens <= local.time() <= self.closes

    def next_open_after(self, moment: datetime) -> datetime:
        """下一次這個市場開盤是什麼時候（當地時間）。

        跟這個模組其他地方一樣，**假日當成有開**——多醒一次只是浪費一次請求，而少醒
        一次是那一天的提醒沒有人在看。方向不對稱，所以往「多醒」錯。

        用當地時間做加減（`aware + timedelta` 走的是牆上時鐘），所以夏令時間換算之後
        開盤還是同一個鐘點，不會整段偏一小時。
        """
        local = moment.astimezone(self.tz)
        candidate = local.replace(
            hour=self.opens.hour, minute=self.opens.minute, second=0, microsecond=0
        )
        if candidate <= local or local.weekday() >= 5:
            candidate += timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate


# 09:00-13:30 for TWSE and TPEx alike. Deliberately the regular session only:
# 盤後定價 and 零股 have their own windows, and the app has no concept of an
# order type that would distinguish them (see CLAUDE.md -- this is an alerting
# product, not an order-routing one).
_TAIWAN = _Session("Asia/Taipei", time(9, 0), time(13, 30))

# Regular US session. Pre- and post-market are excluded because yfinance's
# `fast_info` lastPrice does not serve them either, so pretending the market
# is open then would only feed strategies the previous close again.
_US = _Session("America/New_York", time(9, 30), time(16, 0))


def session_close_after(
    symbol: str, data_source: DataSource, opened_at: datetime
) -> datetime | None:
    """When the trading day containing `opened_at` stops accepting trades.

    None means 「this market never closes」 (crypto) or 「cannot tell」, and the
    caller must then fall back to plain calendar arithmetic rather than
    inventing a close.

    Used by closed_bars() to release an intraday candle that the session cut
    short. Yahoo aligns intraday candles to each exchange's own open, so a
    Taiwanese 4h candle opening at 13:00 is finished at 13:30 -- and the flat
    four-hour arithmetic would otherwise hold it back until 17:00.
    """
    session = _session_for(symbol, data_source)
    if session is None:
        return None
    local = opened_at.astimezone(session.tz)
    return local.replace(
        hour=session.closes.hour,
        minute=session.closes.minute,
        second=0,
        microsecond=0,
    )


def _session_for(symbol: str, data_source: DataSource) -> _Session | None:
    """None means "cannot tell", which callers must read as open."""
    upper = symbol.upper()
    if data_source == DataSource.BINANCE:
        if upper.endswith((".TW", ".TWO")):
            # Binance does not list Taiwanese equities, so this pairing is a
            # mistake -- refused at the input now, but rows predating that
            # check still exist and 「always open」 means polling a shut market
            # every five seconds all night against a source that cannot price
            # it. Same precedent as the bare numeric code below.
            return _TAIWAN
        return None  # crypto trades continuously; nothing to be closed
    if symbol_search.is_crypto_pair(upper):
        # BTC-USD, ETH-USD: yfinance's own crypto tickers. Real, priceable and
        # traded around the clock. The bare-ticker rule below has no dot to go
        # on, so it read these as US equities and called them 閉市 from 16:00
        # to 09:30 New York -- the hours crypto actually moves. A stop-loss on
        # one was never checked overnight and nothing said so.
        return None
    if upper.endswith((".TW", ".TWO")):
        return _TAIWAN
    if "." not in upper:
        if upper.isdigit():
            # An all-numeric symbol is a Taiwanese code that lost its suffix,
            # never a US ticker. Treating it as US made it 閉市 for the entire
            # Taiwan session, so a strategy on it never ran and its stop-loss
            # was never checked -- during exactly the hours that mattered.
            # Schema validation now refuses to store one of these at all; this
            # is the defence for rows that predate that.
            return _TAIWAN
        # Bare tickers are US listings by convention here; every other market
        # yfinance serves carries a suffix (.HK, .T, .L) this does not model,
        # and those fall through to "cannot tell".
        return _US
    return None


def is_open(
    symbol: str,
    at: datetime | None = None,
    data_source: DataSource = DataSource.YFINANCE,
) -> bool:
    moment = at or datetime.now(UTC)
    if moment.tzinfo is None:
        # Everything this app stores is UTC. Reading a naive value as server
        # local time would shift the whole session by however the host is
        # configured, and the container runs in UTC while the owner does not.
        moment = moment.replace(tzinfo=UTC)

    session = _session_for(symbol, data_source)
    if session is None:
        return True
    return session.is_open_at(moment)


def any_open(
    watched: list[tuple[str, DataSource]],
    at: datetime | None = None,
) -> bool:
    """Whether any watched symbol's market is trading right now.

    An empty list is False rather than True: nothing is being watched, so
    there is no market work to do. That is not the same as everything being
    shut, and callers should still run whatever else they owe -- the
    notification retry sweep does not care what time it is.
    """
    return any(is_open(symbol, at, source) for symbol, source in watched)


def seconds_until_next_open(
    watched: list[tuple[str, DataSource]],
    at: datetime | None = None,
) -> float | None:
    """最快多久之後會有一個被盯的市場開盤。全部都問不出來就回 None。

    ＊ 為什麼需要它。

    收盤後的輪詢放慢到半小時，是為了讓免費方案的資料庫睡得著（見
    `market_loop.CLOSED_POLL_INTERVAL_SEC`）。但迴圈是睡滿一整段才醒的，所以單純
    放慢會讓它可能睡過開盤——而開盤那一段正是跳最兇、停損最可能被穿過去的時候。

    **回 None 而不是 0。** 0 會讓呼叫端立刻再跑一輪，也就是忙碌空轉；而「問不出來」
    在這個模組裡一律讀成「有開」，那種情況根本不會走到這裡。
    """
    moment = at or datetime.now(UTC)
    soonest: float | None = None
    for symbol, source in watched:
        session = _session_for(symbol, source)
        if session is None:
            continue
        gap = (session.next_open_after(moment) - moment).total_seconds()
        soonest = gap if soonest is None else min(soonest, gap)
    return soonest
