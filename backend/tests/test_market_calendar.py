"""When is this symbol's market actually open.

Nothing in the app knew. The consequences all landed at night:

- One symbol was polled roughly 5,760 times a day, most of it at a closed
  market, off a scraper that blocks IPs for exactly that.
- on_tick strategies were fed the same closing price thousands of times, so
  their internal averages drifted away from anything real before the next
  session even opened.
- The stop-loss scan compared that stale close against the entry price and
  filed a pending SELL at 3am. It expired 180 minutes later, the next poll
  filed another, and the owner's phone went off several times a night.
- A daily-bar strategy's signal arrives after the close by definition, and
  the pending order it created was always expired before the market reopened.

The rule this module is built on: only suppress activity when the market is
*known* closed. Weekends and clock times outside the session are known.
Holidays are not, and are deliberately treated as open -- polling a shut
market wastes requests, but skipping a real trading day misses the trade.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.enums import DataSource
from app.services import market_calendar as cal

# This file is the calendar's own test: the conftest fixture that stubs it
# out everywhere else has to be off here.
pytestmark = pytest.mark.real_market_hours

TPE = ZoneInfo("Asia/Taipei")
NYC = ZoneInfo("America/New_York")


def test_taiwan_stocks_are_open_during_the_session():
    # Wednesday 10:00 Taipei, mid-session.
    assert cal.is_open("2330.TW", datetime(2026, 8, 19, 10, 0, tzinfo=TPE))


def test_taiwan_stocks_are_closed_after_the_one_thirty_bell():
    # 13:30 is the close, so 13:31 is shut.
    assert not cal.is_open("2330.TW", datetime(2026, 8, 19, 13, 31, tzinfo=TPE))


def test_taiwan_stocks_are_closed_before_the_open():
    assert not cal.is_open("2330.TW", datetime(2026, 8, 19, 8, 59, tzinfo=TPE))


def test_over_the_counter_taiwan_symbols_follow_the_same_session():
    assert cal.is_open("6488.TWO", datetime(2026, 8, 19, 10, 0, tzinfo=TPE))
    assert not cal.is_open("6488.TWO", datetime(2026, 8, 19, 20, 0, tzinfo=TPE))


def test_weekends_are_closed_everywhere():
    # 2026-08-22 is a Saturday.
    assert not cal.is_open("2330.TW", datetime(2026, 8, 22, 10, 0, tzinfo=TPE))
    assert not cal.is_open("AAPL", datetime(2026, 8, 22, 10, 0, tzinfo=NYC))


def test_us_stocks_follow_new_york_hours():
    assert cal.is_open("AAPL", datetime(2026, 8, 19, 10, 0, tzinfo=NYC))
    assert not cal.is_open("AAPL", datetime(2026, 8, 19, 16, 30, tzinfo=NYC))


def test_us_hours_are_read_in_new_york_time_not_the_servers():
    """The container runs in UTC. 14:00 UTC is 10:00 in New York and the
    market is open; reading it as a local clock time would call it shut."""
    assert cal.is_open("AAPL", datetime(2026, 8, 19, 14, 0, tzinfo=UTC))


def test_crypto_never_closes():
    assert cal.is_open("BTCUSDT", datetime(2026, 8, 22, 3, 0, tzinfo=UTC), DataSource.BINANCE)
    assert cal.is_open("BTCUSDT", datetime(2026, 8, 19, 3, 0, tzinfo=UTC), DataSource.BINANCE)


def test_a_symbol_whose_market_cannot_be_told_is_assumed_open():
    """Guessing shut on an unknown symbol would silently stop watching it.
    Guessing open only costs requests."""
    assert cal.is_open("SOMETHING.WEIRD", datetime(2026, 8, 22, 3, 0, tzinfo=UTC))


def test_a_naive_timestamp_is_read_as_utc_not_as_local():
    """Everything this app stores is UTC. Reading a naive value as server
    local time would shift the session by however the host is configured."""
    # 2026-08-19 06:00 UTC = 14:00 Taipei, which is after the 13:30 close.
    assert not cal.is_open("2330.TW", datetime(2026, 8, 19, 6, 0))
    # 2026-08-19 02:00 UTC = 10:00 Taipei, mid-session.
    assert cal.is_open("2330.TW", datetime(2026, 8, 19, 2, 0))


def test_any_open_market_means_the_worker_has_work():
    """The poll is skipped only when everything being watched is shut."""
    when = datetime(2026, 8, 19, 3, 0, tzinfo=UTC)  # 11:00 Taipei, 23:00 NY
    assert cal.any_open([("2330.TW", DataSource.YFINANCE)], when)
    assert not cal.any_open([("AAPL", DataSource.YFINANCE)], when)
    assert cal.any_open([("AAPL", DataSource.YFINANCE), ("2330.TW", DataSource.YFINANCE)], when), (
        "one open market is enough"
    )


def test_nothing_to_watch_is_not_the_same_as_a_closed_market():
    """An account with no strategies and no positions asks for nothing. The
    loop should still turn -- it has other work, like the retry sweep."""
    assert not cal.any_open([], datetime(2026, 8, 19, 3, 0, tzinfo=UTC))


def test_holidays_are_knowingly_treated_as_open():
    """Documented, not an oversight: Taiwan's holidays follow the lunar
    calendar and shift yearly, and a stale table that wrongly says 休市 would
    stop the app watching on a real trading day. Wasting a day of requests is
    the cheaper mistake."""
    # 2026-01-01 is a Thursday and a public holiday in both markets.
    assert cal.is_open("2330.TW", datetime(2026, 1, 1, 10, 0, tzinfo=TPE))


def test_both_time_zones_resolve_at_all():
    """A canary for the deploy image. zoneinfo needs either a system tz
    database or the `tzdata` package; Windows has no system one and
    python:3.13-slim does not ship one either, so if tzdata ever stops being
    installed the calendar raises at import -- and market_loop imports it, and
    main imports that, so the container simply never starts. Cheaper to fail
    here."""
    assert ZoneInfo("Asia/Taipei") is not None
    assert ZoneInfo("America/New_York") is not None


# --- 加密貨幣沒有收盤鐘 -------------------------------------------------------
#
# market_loop 檢查停損停利和委託逾期時，呼叫 is_open() **沒有帶 data_source**（策略那
# 一處有帶，這兩處沒有）。而 Position 和 Order 都沒有 data_source 欄位，所以預設走
# YFINANCE，於是一個裸的 BTCUSDT 沒有點、不是全數字 → 被判成美股。
#
# 後果：加密貨幣部位的停損只在美股 09:30–16:00（週一到五）被檢查過，週末整個跳過——
# 而那正是它在動的時候。market_loop 自己的註解已經寫著「Crypto has no closing bell and
# is therefore never held」，意圖本來就在，只是沒生效。


def test_a_bare_binance_pair_is_never_closed():
    """BTCUSDT、ETHUSDC 這種沒有分隔符號的交易對。

    BTC-USD（yfinance 的寫法）早就判對了；漏的是幣安的寫法，而那是 DataSource.BINANCE
    的使用者會拿到的代號形狀。沒有一個美股代號長這樣——美股代號不會以 USDT 結尾。
    """
    from datetime import UTC, datetime

    from app.services import market_calendar

    # 週六凌晨三點的紐約，美股關得最死的時候。
    saturday = datetime(2026, 8, 22, 7, 0, tzinfo=UTC)

    # ETHBTC 也在裡面：它是真的交易對，而 market_mismatch 早就會拒絕拿 yfinance 去抓
    # 它——輸入端說「這不是美股」，行事曆卻說是，那是同一個事實兩套規則。第一版我另開
    # 了一條只認穩定幣的樣式，就漏掉了它。
    for pair in ("BTCUSDT", "ETHUSDT", "SOLUSDC", "ETHBTC"):
        assert market_calendar.is_open(pair, at=saturday), (
            f"{pair} 被判成閉市——它的停損整個週末不會被檢查，而那正是它在動的時候"
        )


def test_a_real_us_ticker_is_still_closed_at_the_weekend():
    """而美股不可以因此變成永遠開著。

    這一條是上一條的另一半：把判斷放寬到「看不懂就當開著」會讓每一個打錯的代號在半夜
    被輪詢，並且在收盤後用最後一筆收盤價去比停損——那正是 market_loop 註解裡說「filed a
    SELL at 3am that nobody could act on」的那個 bug。
    """
    from datetime import UTC, datetime

    from app.services import market_calendar

    saturday = datetime(2026, 8, 22, 7, 0, tzinfo=UTC)

    # USDT 自己只有四個字，短於 _UNMISTAKABLE_PAIR_MIN_LEN，所以它不會被誤判成交易對。
    for ticker in ("AAPL", "TSLA", "USDT", "USDU"):
        assert not market_calendar.is_open(ticker, at=saturday), f"{ticker} 不該永遠開著"
