"""收盤後放慢是必要的，但不可以慢到睡過開盤。

＊ 為什麼要放慢。

Neon 免費方案給的是每月的運算時數（100 CU-hours ＝ 0.25 CU 跑 400 小時），閒置五分鐘
才休眠而且關不掉。而收盤後每 5 分鐘問一次，剛好踩在那個門檻上——資料庫幾乎不休眠，一
個月要 730 小時，大約第 17 天額度用完，然後停到下一個帳單週期。停著的那半個月，一則
提醒都不會送出。

放慢到 30 分鐘，資料庫就有 25 分鐘是睡著的，整個月用得掉的降到 400 小時以內。

＊ 而放慢會踩到的坑，就在這個檔案裡守著。

迴圈是「睡滿一整段才醒」的，而它原本沒有任何「睡到開盤就好」的概念。所以單純把數字
從 5 分鐘改成 30 分鐘，最壞情況是

    08:35 決定睡 30 分鐘 → 09:05 才醒 → 開盤後前 5 分鐘沒有人在盯

而開盤那一段正是跳最兇、停損最可能被穿過去的時候。**省額度不可以用開盤的那幾分鐘去
換。**

所以放慢的同時要加一條上限：下一次開盤比那一段短的話，就只睡到開盤。這樣收盤後的代
價只剩「日線訊號最多晚 30 分鐘」——而那段時間市場是關的，他本來就要等到隔天才動得
了。
"""

from datetime import UTC, datetime

import pytest

from app.config import settings
from app.enums import DataSource
from app.services import market_calendar, market_loop

pytestmark = pytest.mark.real_market_hours

TW = [("2330.TW", DataSource.YFINANCE)]

# 2026-08-19 是星期三。台股 09:00–13:30（台北）。
BEFORE_THE_BELL = datetime(2026, 8, 19, 0, 45, tzinfo=UTC)  # 08:45 台北，離開盤 15 分鐘
DEEP_NIGHT = datetime(2026, 8, 18, 18, 0, tzinfo=UTC)  # 02:00 台北，離開盤 7 小時
FRIDAY_EVENING = datetime(2026, 8, 21, 8, 0, tzinfo=UTC)  # 週五 16:00 台北


def test_it_does_not_sleep_through_the_opening_bell(monkeypatch):
    """離開盤只剩 15 分鐘的時候，不可以睡 30 分鐘。"""
    monkeypatch.setattr(market_loop, "_last_watched", TW)
    monkeypatch.setattr(market_loop, "_now", lambda: BEFORE_THE_BELL, raising=False)

    delay = market_loop.next_poll_delay(at=BEFORE_THE_BELL)

    assert delay <= 15 * 60 + 1, f"睡了 {delay} 秒，會錯過開盤後的前幾分鐘"


def test_deep_in_the_night_it_uses_the_full_slow_interval(monkeypatch):
    """離開盤還很久的時候就好好睡——那正是省下來的地方。"""
    monkeypatch.setattr(market_loop, "_last_watched", TW)

    assert market_loop.next_poll_delay(at=DEEP_NIGHT) == market_loop.CLOSED_POLL_INTERVAL_SEC


def test_the_weekend_does_not_confuse_it(monkeypatch):
    """週五晚上的「下一次開盤」是週一，不是週六。

    算錯成週六的話它會在整個週末每半小時醒一次——那不會少任何提醒，但省下來的額度
    就沒了，而這個改動的全部理由就是那個額度。
    """
    monkeypatch.setattr(market_loop, "_last_watched", TW)

    assert market_loop.next_poll_delay(at=FRIDAY_EVENING) == market_loop.CLOSED_POLL_INTERVAL_SEC


def test_the_slow_interval_is_long_enough_to_let_the_database_sleep(monkeypatch):
    """Neon 閒置五分鐘才休眠，所以間隔至少要是那個數字的好幾倍。

    這一條盯的是**額度**，不是某個常數叫什麼名字：間隔一旦掉回五分鐘附近，資料庫就
    再也不休眠，而使用者會在月中失去所有提醒。
    """
    assert market_loop.CLOSED_POLL_INTERVAL_SEC >= 15 * 60


def test_it_is_still_fast_while_something_is_trading(monkeypatch):
    """開盤中不受影響——上面那些都只在市場關著的時候才成立。"""
    monkeypatch.setattr(market_loop, "_last_watched", TW)
    trading = datetime(2026, 8, 19, 2, 0, tzinfo=UTC)  # 10:00 台北

    assert market_loop.next_poll_delay(at=trading) == settings.MARKET_DATA_POLL_INTERVAL_SEC


# --- 「下一次開盤是什麼時候」本身 --------------------------------------------


def test_the_next_open_from_before_the_bell_is_today(monkeypatch):
    gap = market_calendar.seconds_until_next_open(TW, at=BEFORE_THE_BELL)

    assert gap == pytest.approx(15 * 60, abs=1)


def test_the_next_open_from_after_the_close_is_tomorrow():
    after_close = datetime(2026, 8, 19, 6, 0, tzinfo=UTC)  # 14:00 台北，已經收盤

    gap = market_calendar.seconds_until_next_open(TW, at=after_close)

    assert gap == pytest.approx(19 * 3600, abs=60)  # 隔天 09:00 台北


def test_the_next_open_from_friday_evening_is_monday():
    gap = market_calendar.seconds_until_next_open(TW, at=FRIDAY_EVENING)

    # 週五 16:00 → 週一 09:00 = 65 小時
    assert gap == pytest.approx(65 * 3600, abs=60)


def test_something_that_never_closes_has_no_next_open():
    """加密貨幣沒有「下一次開盤」，而 `any_open` 本來就會說它是開著的。

    這裡回 None 而不是 0：0 會讓迴圈忙碌空轉，而那比睡太久嚴重得多。
    """
    assert market_calendar.seconds_until_next_open([("BTC-USD", DataSource.YFINANCE)]) is None
