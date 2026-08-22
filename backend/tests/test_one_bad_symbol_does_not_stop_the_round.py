"""一個代號抓不到，不能讓這一輪的每一則警告都不發。

MEASURED: `tick_once` 的 `service.get_bars(...)` 完全沒有包 try，而 `tick_once`
自己的 try 只有 finally 沒有 except。所以一個 provider 例外會一路逃到
`run_forever`，中途跳過的東西是：

    剩下所有策略、停損掃描、訂單過期、以及待重送的通知。

CLAUDE.md：**警告不能停擺是最高優先。** 一個代號被 Yahoo 擋掉，跟「這一輪所有
警告都沒送」之間的差別，就是這個檔案要守住的東西。

WHY THIS DOES NOT ROUTE INTO _record_strategy_error（issue #19 原本這樣寫，這裡
刻意不照做，理由在下面兩條測試裡）：那個函式連續 5 次就把策略關掉。輪詢週期是
五秒，所以 Yahoo 限流 25 秒就足以把使用者的每一支策略永久關掉——而限流結束之後
沒有任何東西會把它們打開。那比原本的 bug 更糟：原本是一輪沒送，那個版本是從此
不再送，而且畫面上只寫著「停用」。

抓不到資料不是策略的錯。策略自己丟例外才是——那條路不動，仍然會累積、仍然會在
第五次關掉它，因為壞掉的程式碼不會自己好。
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.models.enums import DataSource
from app.models.strategy import Strategy
from app.models.user import User
from app.services import market_loop
from app.services.market_data.base import Bar, BarFetchError, Quote, Timeframe
from app.services.market_data.service import MarketDataService
from app.services.strategy_runtime import StrategyRegistry

_START = datetime(2026, 1, 5, tzinfo=UTC)


def _source(name: str, symbol: str) -> str:
    return f'''
class Strategy:
    def __init__(self):
        self.name = "{name}"
        self.symbol = "{symbol}"
        self.timeframe = "1wk"
        self.warmup_bars = 1
        self.seen = []

    def on_bar(self, bar) -> str:
        self.seen.append(bar.close)
        return "HOLD"
'''


@pytest.fixture(autouse=True)
def _fresh_registry(monkeypatch):
    monkeypatch.setattr(market_loop, "_registry", StrategyRegistry())


class _HalfBrokenProvider:
    """Serves candles for everything except one symbol, which blows up.

    RuntimeError, not BarFetchError, and the difference is the whole point.
    MarketDataService already catches BarFetchError and serves the stale cache
    (services/market_data/service.py::_fetch_bars) -- that path is handled.
    What is NOT handled is everything else: an upstream library whose response
    shape changed, a KeyError in a parser, a timeout that was never wrapped.
    Those come straight out of get_bars, and in the market loop there was
    nothing to catch them.
    """

    data_source = DataSource.YFINANCE

    def __init__(self, broken_symbol: str) -> None:
        self.broken_symbol = broken_symbol

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        return {
            s: Quote(symbol=s, data_source=self.data_source, price=Decimal(500)) for s in symbols
        }

    def get_bars(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Bar]:
        if symbol == self.broken_symbol:
            raise RuntimeError("上游回了看不懂的東西")
        return [
            Bar(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=_START + timedelta(weeks=i),
                open=close,
                high=close + 1,
                low=close - 1,
                close=close,
                volume=1000.0,
            )
            for i, close in enumerate([100.0, 101.0, 102.0])
        ]


class _RecoveringProvider(_HalfBrokenProvider):
    """Same, but the outage can be lifted mid-test."""

    def __init__(self, broken_symbol: str) -> None:
        super().__init__(broken_symbol)
        self.broken = True

    def get_bars(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Bar]:
        if symbol == self.broken_symbol and self.broken:
            raise RuntimeError("上游回了看不懂的東西")
        return [
            Bar(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=_START + timedelta(weeks=i),
                open=close,
                high=close + 1,
                low=close - 1,
                close=close,
                volume=1000.0,
            )
            for i, close in enumerate([100.0, 101.0, 102.0])
        ]


def _service(provider, clock) -> MarketDataService:
    return MarketDataService(
        providers={DataSource.YFINANCE: provider},
        bar_ttl_sec=dict.fromkeys(Timeframe, 60.0),
        clock=clock,
    )


def _user(db_session) -> User:
    user = User(email="round@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _strategy(db_session, user, name: str, symbol: str) -> Strategy:
    strategy = Strategy(
        user_id=user.id,
        name=name,
        symbol=symbol,
        source_code=_source(name, symbol),
        code_hash=f"hash-{name}",
        is_active=True,
        warmup_bars=1,
    )
    db_session.add(strategy)
    db_session.commit()
    db_session.refresh(strategy)
    return strategy


def _seen(strategy: Strategy) -> list[float]:
    return market_loop._registry.get_or_load(strategy.id, strategy.source_code).instance.seen


# --- 這一輪的其他每一件事都要照跑 -----------------------------------------------------


def test_the_other_strategies_in_the_same_round_still_run(db_session):
    user = _user(db_session)
    broken = _strategy(db_session, user, "broken", "BAD.TW")
    healthy = _strategy(db_session, user, "healthy", "2330.TW")
    service = _service(_HalfBrokenProvider("BAD.TW"), lambda: 0.0)

    market_loop.tick_once(db=db_session, market_data_service=service)

    assert _seen(healthy) != []
    assert _seen(broken) == []


def test_the_notification_resend_sweep_still_runs(db_session, monkeypatch):
    """這是最重要的一條：待重送的通知是「已經該送而還沒送到」的那些。
    一個抓不到的代號讓它們整輪不被掃，就是這個產品最不能發生的事。"""
    calls: list[str] = []
    monkeypatch.setattr(
        market_loop.notification_retry, "retry_pending", lambda session: calls.append("swept")
    )
    user = _user(db_session)
    _strategy(db_session, user, "broken", "BAD.TW")
    service = _service(_HalfBrokenProvider("BAD.TW"), lambda: 0.0)

    market_loop.tick_once(db=db_session, market_data_service=service)

    assert calls == ["swept"]


def test_the_stop_loss_scan_and_order_expiry_still_run(db_session, monkeypatch):
    """`_expire_stale_orders` 排在部位掃描後面的同一段直線程式碼裡，所以它被呼叫
    到，就代表中間那一段也走完了。"""
    calls: list[str] = []
    monkeypatch.setattr(
        market_loop,
        "_expire_stale_orders",
        lambda session, events: calls.append("expired"),
    )
    user = _user(db_session)
    _strategy(db_session, user, "broken", "BAD.TW")
    service = _service(_HalfBrokenProvider("BAD.TW"), lambda: 0.0)

    market_loop.tick_once(db=db_session, market_data_service=service)

    assert calls == ["expired"]


def test_the_exception_never_reaches_the_caller(db_session):
    """`run_forever` 的 except 會吞掉它並睡到下一輪——也就是這一輪的其餘部分
    全部沒發生，而且沒有人看得出來。"""
    user = _user(db_session)
    _strategy(db_session, user, "broken", "BAD.TW")
    service = _service(_HalfBrokenProvider("BAD.TW"), lambda: 0.0)

    events = market_loop.tick_once(db=db_session, market_data_service=service)

    assert isinstance(events, list)


# --- 抓不到不是策略的錯 ---------------------------------------------------------------


def test_a_provider_outage_never_switches_the_strategy_off(db_session):
    """輪詢是五秒一次。連續五次就關掉的話，Yahoo 擋你 25 秒，使用者的提醒就從此
    不再送——而限流結束之後沒有任何東西會把它打開。"""
    user = _user(db_session)
    broken = _strategy(db_session, user, "broken", "BAD.TW")
    provider = _HalfBrokenProvider("BAD.TW")
    time = {"t": 0.0}
    service = _service(provider, lambda: time["t"])

    for _ in range(10):
        time["t"] += 61.0
        market_loop.tick_once(db=db_session, market_data_service=service)

    db_session.refresh(broken)
    assert broken.is_active is True
    assert broken.consecutive_errors == 0


def test_but_it_says_why_that_strategy_is_not_doing_anything(db_session):
    """安靜地什麼都不做，跟壞掉沒有分別。那一列要說得出原因。"""
    user = _user(db_session)
    broken = _strategy(db_session, user, "broken", "BAD.TW")
    service = _service(_HalfBrokenProvider("BAD.TW"), lambda: 0.0)

    market_loop.tick_once(db=db_session, market_data_service=service)

    db_session.refresh(broken)
    assert broken.last_error
    assert "看不懂" in broken.last_error


def test_and_takes_it_back_when_the_feed_comes_good(db_session):
    """留著一句已經不成立的理由，等於教人不要相信那一欄。"""
    user = _user(db_session)
    strategy = _strategy(db_session, user, "recovering", "BAD.TW")
    provider = _RecoveringProvider("BAD.TW")
    time = {"t": 0.0}
    service = _service(provider, lambda: time["t"])

    market_loop.tick_once(db=db_session, market_data_service=service)
    db_session.refresh(strategy)
    assert strategy.last_error

    provider.broken = False
    time["t"] += 61.0
    market_loop.tick_once(db=db_session, market_data_service=service)

    db_session.refresh(strategy)
    assert strategy.last_error is None


def test_a_strategy_that_raises_is_still_treated_as_broken(db_session):
    """這條是反面的界線：程式碼自己丟例外仍然會累積、仍然會在第五次被關掉。
    壞掉的程式碼不會自己好，而抓不到資料會。"""
    user = _user(db_session)
    boom = Strategy(
        user_id=user.id,
        name="boom",
        symbol="2330.TW",
        source_code=(
            "class Strategy:\n"
            "    def __init__(self):\n"
            "        self.name = 'boom'\n"
            "        self.symbol = '2330.TW'\n"
            "        self.timeframe = '1wk'\n"
            "        self.warmup_bars = 1\n"
            "\n"
            "    def on_bar(self, bar) -> str:\n"
            "        raise RuntimeError('boom')\n"
        ),
        code_hash="hash-boom",
        is_active=True,
        warmup_bars=1,
    )
    db_session.add(boom)
    db_session.commit()
    provider = _HalfBrokenProvider("NOTHING.TW")
    time = {"t": 0.0}
    service = _service(provider, lambda: time["t"])

    for _ in range(6):
        time["t"] += 61.0
        market_loop.tick_once(db=db_session, market_data_service=service)

    db_session.refresh(boom)
    assert boom.is_active is False


def test_a_rate_limit_alone_was_already_handled_below_this_layer(db_session):
    """界線寫下來，免得下一個人以為這裡在重複處理同一件事。

    BarFetchError 在 MarketDataService 就被接住了：它會端出上一次的快取而不是把
    失敗存成「這個代號沒有歷史資料」。那條路不需要這裡再做什麼——會逃上來的是
    「沒有被包成 BarFetchError 的那些」。
    """
    user = _user(db_session)
    strategy = _strategy(db_session, user, "rate-limited", "BAD.TW")

    class _RateLimited(_HalfBrokenProvider):
        def get_bars(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Bar]:
            if symbol == self.broken_symbol:
                raise BarFetchError("429 Too Many Requests")
            return []

    service = _service(_RateLimited("BAD.TW"), lambda: 0.0)

    market_loop.tick_once(db=db_session, market_data_service=service)

    db_session.refresh(strategy)
    assert strategy.is_active is True
